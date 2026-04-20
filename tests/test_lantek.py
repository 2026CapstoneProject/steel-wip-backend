# tests/test_lantek.py
"""
LANTEK API 테스트
  - GET    /api/lantek/get/{scenario_id}  (LANTEK 결과 조회)
  - POST   /api/lantek/import             (LANTEK 더미 데이터 import)
  - DELETE /api/lantek/delete             (LANTEK 데이터 초기화)

[전제조건]
  - POST /api/lantek/import 은 IN_STOCK 상태의 SteelWip이 최소 1개 있어야 성공한다.
  - import 성공 시 LazerCutting 12개(3배치 × 4커팅)와
    각 커팅당 0~2개의 EstimatedWips가 생성된다.
  - import 성공 시 시나리오 status가 None → DRAFT 로 변경된다.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services import lantek_service
from app.models import (
    Projects, Scenarios, Batch, BatchItems, SteelWip,
    LazerCutting, EstimatedWips, QrCodes, Locations
)
from datetime import date, datetime


# ══════════════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════════════

async def make_project(
    db: AsyncSession,
    title: str = "LANTEK 테스트 프로젝트",
    due: date = date(2026, 12, 31),
) -> Projects:
    project = Projects(title=title, project_due=due)
    db.add(project)
    await db.flush()
    return project


async def make_scenario(
    db: AsyncSession,
    project_id: int,
    status: str | None = None,
    title: str = "LANTEK 테스트 시나리오-1",
) -> Scenarios:
    scenario = Scenarios(
        title=title,
        status=status,
        scenario_due=date(2026, 12, 31),
        scenario_order=0,
        lazer_name="LAZER1",
        emergency_or_not=False,
        created_at=datetime.now(),
        project_id=project_id,
    )
    db.add(scenario)
    await db.flush()
    return scenario


async def make_wip_in_stock(
    db: AsyncSession,
    material: str = "SM355A",
) -> SteelWip:
    wip = SteelWip(
        status="IN_STOCK",
        material=material,
        thickness=20.0,
        width=2438.0,
        length=6096.0,
        weight=100.0,
        manufacturer="POSCO",
    )
    db.add(wip)
    await db.flush()
    return wip


async def make_lazer_cutting(
    db: AsyncSession,
    scenario_id: int,
    wip_id: int | None = None,
    estimated_cutting_time: int = 60,
) -> LazerCutting:
    lc = LazerCutting(
        scenario_id=scenario_id,
        steel_wip_id=wip_id,
        estimated_cutting_time=estimated_cutting_time,
        status="PENDING",
        priority="LOW",
    )
    db.add(lc)
    await db.flush()
    return lc


async def make_estimated_wip(
    db: AsyncSession,
    lazer_cutting_id: int,
    qr_id: int | None = None,
) -> EstimatedWips:
    ew = EstimatedWips(
        lazer_cutting_id=lazer_cutting_id,
        qr_id=qr_id,
        manufacturer="POSCO",
        material="SM355A",
        thickness=10.0,
        width=500.0,
        length=1000.0,
        weight=40.0,
    )
    db.add(ew)
    await db.flush()
    return ew


def test_parse_layouts_supports_demo_english_template():
    text = """
REPORT PAGE 1
PART SUMMARY
JOB NAME : Job2
SOURCE WIP ID : 28
OUTPUT WIP ID : 103
OUTPUT SIZE : 950.00mm*1690.00mm
SLAB SIZE : 420.00mm*1500.00mm
PLATE SIZE : 1200.00mm*2500.00mm
CUTTING TIME HOURS : 0.75
THICKNESS : 12.00mm
MATERIAL : GS400
LAYOUT 1-1/2

REPORT PAGE 2
PART SUMMARY
SLAB SIZE : 1190.00mm*570.00mm
PLATE SIZE : 1200.00mm*2500.00mm
CUTTING TIME HOURS : 0.07
THICKNESS : 20.00mm
MATERIAL : SS275
LAYOUT 2-2/2
"""

    layouts = lantek_service._parse_layouts_from_text(text)

    assert len(layouts) == 2
    assert layouts[0].layout_name == "1-1/2"
    assert layouts[0].material == "GS400"
    assert layouts[0].thickness == 12.0
    assert layouts[0].slab_width == 420.0
    assert layouts[0].job_name == "Job2"
    assert layouts[0].planned_source_wip_id == 28
    assert layouts[0].planned_output_wip_id == 103
    assert layouts[0].output_width == 950.0
    assert layouts[0].output_length == 1690.0
    assert layouts[1].layout_name == "2-2/2"
    assert layouts[1].material == "SS275"
    assert layouts[1].thickness == 20.0


# ══════════════════════════════════════════════════════════════════════
# GET /api/lantek/get/{scenario_id} — LANTEK 결과 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_lantek_no_data(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 시나리오 ID → 빈 배열"""
    response = await client.get("/api/lantek/get/99999")

    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_get_lantek_scenario_no_cuttings(client: AsyncClient, db_session: AsyncSession):
    """시나리오는 있지만 LazerCutting이 없으면 lazerCutting 배열이 비어 있다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    await db_session.commit()

    response = await client.get(f"/api/lantek/get/{scenario.id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["scenarioId"] == scenario.id
    assert data[0]["projectId"] == project.id
    assert data[0]["lazerCutting"] == []


@pytest.mark.asyncio
async def test_get_lantek_with_cuttings(client: AsyncClient, db_session: AsyncSession):
    """LazerCutting + EstimatedWips 포함 시 올바른 구조 반환"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    wip = await make_wip_in_stock(db_session)

    lc = await make_lazer_cutting(db_session, scenario.id, wip_id=wip.id, estimated_cutting_time=90)

    qr = QrCodes(qr_code="QR-LANTEK-1")
    db_session.add(qr)
    await db_session.flush()
    await make_estimated_wip(db_session, lc.id, qr_id=qr.id)
    await db_session.commit()

    response = await client.get(f"/api/lantek/get/{scenario.id}")

    assert response.status_code == 200
    data = response.json()["data"][0]

    # 기본 메타
    assert data["scenarioId"] == scenario.id
    assert data["lazerName"] == "LAZER1"

    # LazerCutting 검증
    assert len(data["lazerCutting"]) == 1
    lc_data = data["lazerCutting"][0]
    assert lc_data["id"] == lc.id
    # estimatedCuttingTime: 90분 → "01:30"
    assert lc_data["estimatedCuttingTime"] == "01:30"

    # input 검증
    assert lc_data["input"]["material"] == "SM355A"
    assert lc_data["input"]["thickness"] == 20.0

    # EstimatedWips 검증
    assert len(lc_data["estimatedWips"]) == 1
    ew_data = lc_data["estimatedWips"][0]
    assert ew_data["thickness"] == 10.0
    assert ew_data["width"] == 500.0
    assert ew_data["height"] == 1000.0  # DB length → JSON height


@pytest.mark.asyncio
async def test_get_lantek_cutting_time_format(client: AsyncClient, db_session: AsyncSession):
    """estimatedCuttingTime이 'HH:MM' 형식으로 변환된다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")

    # 65분 → "01:05"
    await make_lazer_cutting(db_session, scenario.id, estimated_cutting_time=65)
    # 30분 → "00:30"
    await make_lazer_cutting(db_session, scenario.id, estimated_cutting_time=30)
    await db_session.commit()

    response = await client.get(f"/api/lantek/get/{scenario.id}")

    lc_list = response.json()["data"][0]["lazerCutting"]
    times = {lc["estimatedCuttingTime"] for lc in lc_list}
    assert "01:05" in times
    assert "00:30" in times


# ══════════════════════════════════════════════════════════════════════
# POST /api/lantek/import — LANTEK 더미 데이터 import
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_import_lantek_success(client: AsyncClient, db_session: AsyncSession):
    """
    IN_STOCK WIP이 있으면 import 성공 —
    LazerCutting 12개 생성 + 시나리오 status → DRAFT
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status=None)
    # IN_STOCK WIP 10개 준비
    for _ in range(10):
        await make_wip_in_stock(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("dummy.pdf", b"dummy content", "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 201

    # 시나리오 상태 DRAFT 확인
    await db_session.refresh(scenario)
    assert scenario.status == "DRAFT"

    # LazerCutting 12개 생성 확인
    lc_count_result = await db_session.execute(
        select(LazerCutting).where(LazerCutting.scenario_id == scenario.id)
    )
    lc_list = lc_count_result.scalars().all()
    assert len(lc_list) == 12

    batch_list = (
        await db_session.execute(select(Batch).where(Batch.scenario_id == scenario.id))
    ).scalars().all()
    batch_item_list = (
        await db_session.execute(
            select(BatchItems).join(Batch, BatchItems.batch_id == Batch.id).where(Batch.scenario_id == scenario.id)
        )
    ).scalars().all()
    assert len(batch_list) == 3
    assert len(batch_item_list) > 0


@pytest.mark.asyncio
async def test_import_lantek_no_stock(client: AsyncClient, db_session: AsyncSession):
    """
    IN_STOCK WIP이 없으면 import 실패 → 400
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status=None)
    # WIP 없음 (또는 CONSUMED 상태만 있음)
    wip = SteelWip(
        status="CONSUMED",
        material="SM355A",
        thickness=20.0, width=2438.0, length=6096.0, weight=100.0,
        manufacturer="POSCO",
    )
    db_session.add(wip)
    await db_session.commit()

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("dummy.pdf", b"dummy content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "가용 가능한 재고" in response.json()["message"]


@pytest.mark.asyncio
async def test_import_lantek_returns_scenario_data(client: AsyncClient, db_session: AsyncSession):
    """import 후 응답에 시나리오 + LazerCutting 데이터가 포함된다"""
    project = await make_project(db_session, title="import 검증 프로젝트")
    scenario = await make_scenario(db_session, project.id, status=None)
    for _ in range(5):
        await make_wip_in_stock(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("test.pdf", b"content", "application/pdf")},
    )

    data = response.json()["data"][0]
    assert data["scenarioId"] == scenario.id
    assert data["projectTitle"] == "import 검증 프로젝트"
    # LazerCutting 12개
    assert len(data["lazerCutting"]) == 12


@pytest.mark.asyncio
async def test_import_lantek_parses_pdf_layouts(monkeypatch, client: AsyncClient, db_session: AsyncSession):
    """
    PDF에서 레이아웃 2개가 파싱되면 실제 절단 지시 2개와 예상 잔재 2개가 생성된다.
    각 레이아웃은 재질/두께가 맞는 IN_STOCK WIP에 연결된다.
    """
    project = await make_project(db_session, title="파싱 프로젝트")
    scenario = await make_scenario(db_session, project.id, status=None)
    wip1 = await make_wip_in_stock(db_session, material="SM355A")
    wip2 = await make_wip_in_stock(db_session, material="SM355A")
    wip1.thickness = 12.0
    wip2.thickness = 12.0
    await db_session.commit()

    sample_text = """
부품 정보 요약 총 종 개 부품 : 12 62
슬랩 사이즈 : 2421.90mm*6079.10mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 0.94가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 1-1/5
부품 정보 요약 총 종 개 부품 : 7 64
슬랩 사이즈 : 2198.62mm*1251.10mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 0.13가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 5-5/5
"""
    monkeypatch.setattr(lantek_service, "_extract_pdf_text", lambda file_bytes: sample_text)

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("parsed.pdf", b"%PDF-test", "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert len(data["lazerCutting"]) == 2
    assert data["lazerCutting"][0]["estimatedCuttingTime"] == "00:56"
    assert data["lazerCutting"][1]["estimatedWips"][0]["width"] == 2198.62
    assert data["lazerCutting"][1]["estimatedWips"][0]["height"] == 1251.1

    cuttings = (
        await db_session.execute(select(LazerCutting).where(LazerCutting.scenario_id == scenario.id))
    ).scalars().all()
    assert len(cuttings) == 2
    assert {cut.steel_wip_id for cut in cuttings} == {wip1.id, wip2.id}


@pytest.mark.asyncio
async def test_import_lantek_parsed_scrap_weight(monkeypatch, client: AsyncClient, db_session: AsyncSession):
    """
    PDF 파싱 경로에서는 슬랩 사이즈를 예상 잔재로 저장하고 무게도 계산한다.
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status=None)
    source_wip = await make_wip_in_stock(db_session, material="SM355A")
    source_wip.thickness = 12.0
    await db_session.commit()

    sample_text = """
부품 정보 요약
슬랩 사이즈 : 1000.00mm*2000.00mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 1.00가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 1-1/1
"""
    monkeypatch.setattr(lantek_service, "_extract_pdf_text", lambda file_bytes: sample_text)

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("parsed.pdf", b"%PDF-test", "application/pdf")},
    )

    assert response.status_code == 200
    estimated = response.json()["data"][0]["lazerCutting"][0]["estimatedWips"][0]
    assert estimated["weight"] == 188.4


@pytest.mark.asyncio
async def test_import_lantek_demo_jobs_show_planned_wip_info(monkeypatch, client: AsyncClient, db_session: AsyncSession):
    project = await make_project(db_session, title="데모 작업지시서 프로젝트")
    scenario = await make_scenario(db_session, project.id, status=None)

    for material, thickness in [("SM355A", 12.0), ("SM355A", 12.0), ("SS275", 20.0)]:
        wip = await make_wip_in_stock(db_session, material=material)
        wip.thickness = thickness
    await db_session.commit()

    sample_text = """
PART SUMMARY
JOB NAME : Job1
SOURCE WIP ID : 0
OUTPUT WIP ID : 0
SLAB SIZE : 2438.00mm*6096.00mm
PLATE SIZE : 2438.00mm*6096.00mm
CUTTING TIME HOURS : 4.01
THICKNESS : 12.00mm
MATERIAL : SM355A
LAYOUT 1-1/3
PART SUMMARY
JOB NAME : Job2
SOURCE WIP ID : 28
OUTPUT WIP ID : 103
OUTPUT SIZE : 950.00mm*1690.00mm
SLAB SIZE : 950.00mm*1690.00mm
PLATE SIZE : 950.00mm*2530.00mm
CUTTING TIME HOURS : 0.16
THICKNESS : 12.00mm
MATERIAL : SM355A
LAYOUT 2-2/3
PART SUMMARY
JOB NAME : Job3
SOURCE WIP ID : 99
OUTPUT WIP ID : 104
OUTPUT SIZE : 1190.00mm*570.00mm
SLAB SIZE : 1190.00mm*570.00mm
PLATE SIZE : 570.00mm*2450.00mm
CUTTING TIME HOURS : 0.07
THICKNESS : 20.00mm
MATERIAL : SS275
LAYOUT 3-3/3
"""
    monkeypatch.setattr(lantek_service, "_extract_pdf_text", lambda file_bytes: sample_text)

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("demo.pdf", b"%PDF-demo", "application/pdf")},
    )

    assert response.status_code == 200
    cuttings = response.json()["data"][0]["lazerCutting"]
    assert len(cuttings) == 3
    assert cuttings[0]["jobName"] == "Job1"
    assert cuttings[0]["plannedSourceWipId"] == 0
    assert cuttings[0]["estimatedWips"] == []
    assert cuttings[1]["jobName"] == "Job2"
    assert cuttings[1]["plannedSourceWipId"] == 28
    assert cuttings[1]["estimatedWips"][0]["plannedWipId"] == 103
    assert cuttings[1]["estimatedWips"][0]["width"] == 950.0
    assert cuttings[1]["estimatedWips"][0]["height"] == 1690.0
    assert cuttings[2]["jobName"] == "Job3"
    assert cuttings[2]["plannedSourceWipId"] == 99
    assert cuttings[2]["estimatedWips"][0]["plannedWipId"] == 104


@pytest.mark.asyncio
async def test_import_lantek_parsed_layouts_can_reuse_stock_when_insufficient(
    monkeypatch, client: AsyncClient, db_session: AsyncSession
):
    """
    PDF 레이아웃 수가 실제 IN_STOCK 수보다 많아도
    임시 시나리오 생성 목적이라면 같은 원판을 재사용해 import가 완료되어야 한다.
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status=None)
    source_wip = await make_wip_in_stock(db_session, material="SM355A")
    source_wip.thickness = 12.0
    await db_session.commit()

    sample_text = """
부품 정보 요약
슬랩 사이즈 : 1000.00mm*2000.00mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 1.00가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 1-1/2
부품 정보 요약
슬랩 사이즈 : 800.00mm*1200.00mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 0.50가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 2-2/2
"""
    monkeypatch.setattr(lantek_service, "_extract_pdf_text", lambda file_bytes: sample_text)

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("parsed.pdf", b"%PDF-test", "application/pdf")},
    )

    assert response.status_code == 200
    cuttings = response.json()["data"][0]["lazerCutting"]
    assert len(cuttings) == 2
    assert cuttings[0]["input"]["material"] == "SM355A"
    assert cuttings[1]["input"]["material"] == "SM355A"


@pytest.mark.asyncio
async def test_import_lantek_populates_scenario_result_batch_items(
    monkeypatch, client: AsyncClient, db_session: AsyncSession
):
    """
    import 직후 /api/scenario/{id}에서도 batchItems가 보여야 한다.
    solver를 따로 돌리지 않아도 office result/history 화면이 비지 않도록 보장한다.
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status=None)
    source_wip = await make_wip_in_stock(db_session, material="SM355A")
    source_wip.thickness = 12.0
    source_wip.location_id = 1
    await db_session.commit()

    sample_text = """
부품 정보 요약
슬랩 사이즈 : 800.00mm*1200.00mm
판재 크기 : 2438.00mm*6096.00mm
단일 가공 시간 시간 : 0.50가공 횟수 : 1판재 두께 : 12.00mm판재 재질 : SM355A
레이아웃 1-1/1
"""
    monkeypatch.setattr(lantek_service, "_extract_pdf_text", lambda file_bytes: sample_text)

    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("parsed.pdf", b"%PDF-test", "application/pdf")},
    )
    assert response.status_code == 200

    scenario_response = await client.get(f"/api/scenario/{scenario.id}")
    assert scenario_response.status_code == 200
    batch_items = scenario_response.json()["data"][0]["batchItems"]
    assert len(batch_items) >= 2


# ══════════════════════════════════════════════════════════════════════
# DELETE /api/lantek/delete — LANTEK 데이터 초기화
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_lantek_success(client: AsyncClient, db_session: AsyncSession):
    """LANTEK 데이터 삭제 성공 — LazerCutting, EstimatedWips, QrCodes 제거"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    wip = await make_wip_in_stock(db_session)

    lc = await make_lazer_cutting(db_session, scenario.id, wip_id=wip.id)

    qr = QrCodes(qr_code="QR-DELETE-TEST")
    db_session.add(qr)
    await db_session.flush()
    ew = await make_estimated_wip(db_session, lc.id, qr_id=qr.id)
    await db_session.commit()

    response = await client.request("DELETE", "/api/lantek/delete", json={"scenario_id": scenario.id})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert "초기화" in body["message"]

    # LazerCutting, EstimatedWips 삭제 확인
    assert await db_session.get(LazerCutting, lc.id) is None
    assert await db_session.get(EstimatedWips, ew.id) is None
    assert await db_session.get(QrCodes, qr.id) is None

    # 시나리오 status → None으로 초기화 확인
    await db_session.refresh(scenario)
    assert scenario.status is None


@pytest.mark.asyncio
async def test_delete_lantek_no_cuttings(client: AsyncClient, db_session: AsyncSession):
    """LazerCutting이 없어도 삭제 요청은 성공한다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    await db_session.commit()

    response = await client.request("DELETE", "/api/lantek/delete", json={"scenario_id": scenario.id})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_delete_lantek_does_not_delete_scenario(
    client: AsyncClient, db_session: AsyncSession
):
    """LANTEK 초기화는 시나리오 자체를 삭제하지 않는다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    await make_lazer_cutting(db_session, scenario.id)
    await db_session.commit()

    await client.request("DELETE", "/api/lantek/delete", json={"scenario_id": scenario.id})

    # 시나리오는 여전히 존재해야 함
    existing = await db_session.get(Scenarios, scenario.id)
    assert existing is not None
    assert existing.status is None  # status만 초기화됨


@pytest.mark.asyncio
async def test_delete_lantek_can_reimport(client: AsyncClient, db_session: AsyncSession):
    """LANTEK 초기화 후 재import가 정상 동작한다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    for _ in range(5):
        await make_wip_in_stock(db_session)
    await db_session.commit()

    # 초기화
    await client.request("DELETE", "/api/lantek/delete", json={"scenario_id": scenario.id})

    # 재import
    response = await client.post(
        "/api/lantek/import",
        data={"scenario_id": scenario.id},
        files={"file": ("re-import.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 200
    await db_session.refresh(scenario)
    assert scenario.status == "DRAFT"


# ══════════════════════════════════════════════════════════════════════
# ⑥ LantekEstimatedWip — weight 필드 반환 검증
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_lantek_estimated_wip_returns_weight(
    client: AsyncClient, db_session: AsyncSession
):
    """
    GET /api/lantek/get/{scenario_id} 응답의 estimatedWips 항목에
    weight(절단 후 무게) 필드가 포함되어야 한다.
    make_estimated_wip 헬퍼에서 weight=40.0으로 설정되므로 그 값을 검증한다.
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    wip = await make_wip_in_stock(db_session)
    await db_session.commit()

    lc = await make_lazer_cutting(db_session, scenario.id, wip_id=wip.id, estimated_cutting_time=30)

    qr = QrCodes(qr_code="QR-WEIGHT-TEST")
    db_session.add(qr)
    await db_session.flush()
    await make_estimated_wip(db_session, lc.id, qr_id=qr.id)  # weight=40.0
    await db_session.commit()

    response = await client.get(f"/api/lantek/get/{scenario.id}")

    assert response.status_code == 200
    ew = response.json()["data"][0]["lazerCutting"][0]["estimatedWips"][0]
    assert ew["weight"] == 40.0


@pytest.mark.asyncio
async def test_get_lantek_estimated_wip_weight_none(
    client: AsyncClient, db_session: AsyncSession
):
    """
    EstimatedWips.weight 가 null인 경우 응답에서 weight=null로 반환된다.
    """
    from app.models import EstimatedWips as _EW

    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    wip = await make_wip_in_stock(db_session)
    await db_session.commit()

    lc = await make_lazer_cutting(db_session, scenario.id, wip_id=wip.id, estimated_cutting_time=10)

    # weight=None인 EstimatedWip 직접 생성
    ew_no_weight = _EW(
        lazer_cutting_id=lc.id,
        qr_id=None,
        manufacturer="POSCO",
        material="SM355A",
        thickness=8.0,
        width=400.0,
        length=800.0,
        weight=None,
    )
    db_session.add(ew_no_weight)
    await db_session.commit()

    response = await client.get(f"/api/lantek/get/{scenario.id}")

    assert response.status_code == 200
    ew = response.json()["data"][0]["lazerCutting"][0]["estimatedWips"][0]
    assert ew["weight"] is None
