# tests/test_scenarios.py
"""
시나리오 관련 API 테스트
  - POST   /api/scenario/create       (시나리오 생성 / 재사용)
  - GET    /api/scenario/{id}         (시나리오 결과 조회)
  - DELETE /api/scenario/{id}         (시나리오 삭제)
  - GET    /api/scenario_cart         (DRAFT 시나리오 이력 조회)
  - GET    /api/scenario_send/        (현장 전송 이력 조회)
  - POST   /api/scenario_send/{id}    (시나리오 현장 전송)

[픽스처 규칙]
  - SQLite in-memory 사용 → FK 미강제 → Users 선생성 불필요
  - server_default(now() 등)는 픽스처에서 값을 직접 지정해 우회
"""

import pytest
from datetime import date, datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Projects, Scenarios, Batch, BatchItems, SteelWip,
    LazerCutting, EstimatedWips, QrCodes, Locations
)


# ══════════════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════════════

async def make_project(
    db: AsyncSession,
    title: str = "테스트 프로젝트",
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
    title: str = "테스트 시나리오-1",
    lazer_name: str = "LAZER1",
    order: int = 0,
    emergency: bool = False,
) -> Scenarios:
    scenario = Scenarios(
        title=title,
        status=status,
        scenario_due=date(2026, 12, 31),
        scenario_order=order,
        lazer_name=lazer_name,
        emergency_or_not=emergency,
        created_at=datetime.now(),
        project_id=project_id,
    )
    db.add(scenario)
    await db.flush()
    return scenario


async def make_batch(db: AsyncSession, scenario_id: int, batch_order: int = 1) -> Batch:
    batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
    db.add(batch)
    await db.flush()
    return batch


async def make_location(db: AsyncSession, name: str = "A-1") -> Locations:
    loc = Locations(loc_name=name, loc_can_stock=1, loc_stack_height=10)
    db.add(loc)
    await db.flush()
    return loc


async def make_wip(
    db: AsyncSession,
    status: str = "IN_STOCK",
    location_id: int | None = None,
) -> SteelWip:
    wip = SteelWip(
        status=status,
        material="SM355A",
        thickness=20.0,
        width=2438.0,
        length=6096.0,
        weight=100.0,
        manufacturer="POSCO",
        location_id=location_id,
    )
    db.add(wip)
    await db.flush()
    return wip


async def make_batch_item(
    db: AsyncSession,
    batch_id: int,
    wip_id: int | None,
    from_loc_id: int | None,
    to_loc_id: int | None,
    action: str = "PICKING",
    status: str = "BEFORE_PENDING",
    item_order: int = 1,
) -> BatchItems:
    item = BatchItems(
        batch_id=batch_id,
        steel_wip_id=wip_id,
        batch_item_action=action,
        status=status,
        batch_item_order=item_order,
        from_location=from_loc_id,
        to_location=to_loc_id,
        expected_start_time=0,
        expected_running_time=5,
    )
    db.add(item)
    await db.flush()
    return item


# ══════════════════════════════════════════════════════════════════════
# POST /api/scenario/create — 시나리오 생성 / 재사용
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_scenario_success(client: AsyncClient, db_session: AsyncSession):
    """
    프로젝트가 존재하면 status=None인 새 시나리오가 생성된다.
    생성된 시나리오의 title 은 '{project.title}-1' 형식이다.
    """
    project = await make_project(db_session, title="포스코 건설(80톤)")
    await db_session.commit()

    payload = {"project_id": project.id, "scenario_due": "2026-12-31"}
    response = await client.post("/api/scenario/create", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 201
    data = body["data"]
    assert data["project_id"] == project.id
    assert data["status"] is None
    assert "포스코 건설(80톤)-1" in data["title"]


@pytest.mark.asyncio
async def test_create_scenario_reuses_existing_none_status(
    client: AsyncClient, db_session: AsyncSession
):
    """
    동일 project_id + scenario_due 에 status=None인 시나리오가 이미 있으면
    새로 생성하지 않고 기존 시나리오를 그대로 반환한다.
    """
    project = await make_project(db_session)
    existing = await make_scenario(db_session, project.id, status=None)
    await db_session.commit()

    payload = {"project_id": project.id, "scenario_due": "2026-12-31"}
    response = await client.post("/api/scenario/create", json=payload)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == existing.id  # 기존 시나리오 ID와 동일


@pytest.mark.asyncio
async def test_create_scenario_creates_new_when_all_have_status(
    client: AsyncClient, db_session: AsyncSession
):
    """
    동일 project + due의 시나리오가 모두 DRAFT 이상인 경우 새 비교군 시나리오를 생성한다.
    """
    project = await make_project(db_session)
    await make_scenario(db_session, project.id, status="DRAFT", title="테스트 프로젝트-1")
    await db_session.commit()

    payload = {"project_id": project.id, "scenario_due": "2026-12-31"}
    response = await client.post("/api/scenario/create", json=payload)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] is None
    assert data["title"] == "테스트 프로젝트-1"  # 기존 title 복사


@pytest.mark.asyncio
async def test_create_scenario_project_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """존재하지 않는 project_id → 404"""
    payload = {"project_id": 99999, "scenario_due": "2026-12-31"}
    response = await client.post("/api/scenario/create", json=payload)

    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════
# GET /api/scenario/{scenario_id} — 시나리오 결과 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_scenario_result_empty(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 시나리오 ID → 빈 배열"""
    response = await client.get("/api/scenario/99999")

    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_get_scenario_result_basic(client: AsyncClient, db_session: AsyncSession):
    """시나리오 결과 조회 — 기본 메타 및 통계 검증"""
    project = await make_project(db_session, title="현대 건설(50톤)")
    scenario = await make_scenario(db_session, project.id, status="DRAFT", title="현대 건설(50톤)-1")
    batch = await make_batch(db_session, scenario.id)

    loc_from = await make_location(db_session, "A-1")
    loc_to   = await make_location(db_session, "B-1")
    wip1 = await make_wip(db_session)
    wip2 = await make_wip(db_session)

    # PICKING 2개, RELOCATE 1개
    await make_batch_item(db_session, batch.id, wip1.id, loc_from.id, loc_to.id, action="PICKING", item_order=1)
    await make_batch_item(db_session, batch.id, wip2.id, loc_from.id, loc_to.id, action="PICKING", item_order=2)
    await make_batch_item(db_session, batch.id, wip1.id, loc_from.id, loc_to.id, action="RELOCATE", item_order=3)
    await db_session.commit()

    response = await client.get(f"/api/scenario/{scenario.id}")

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["scenarioId"] == scenario.id
    assert data["projectTitle"] == "현대 건설(50톤)"
    assert data["scenarioTitle"] == "현대 건설(50톤)-1"
    # PICKING 2개 → totalWipNum=2
    assert data["totalWipNum"] == 2
    # 전체 이동 횟수 = 3
    assert data["totalMoveNum"] == 3
    # batchItems 배열 길이 = 3
    assert len(data["batchItems"]) == 3


@pytest.mark.asyncio
async def test_get_scenario_result_action_names_korean(
    client: AsyncClient, db_session: AsyncSession
):
    """batchItemAction이 한글로 변환되어 반환된다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    batch = await make_batch(db_session, scenario.id)

    loc_a = await make_location(db_session, "A-1")
    loc_b = await make_location(db_session, "B-1")
    wip = await make_wip(db_session)

    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="RELOCATE")
    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="PICKING",  item_order=2)
    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="INBOUND",  item_order=3)
    await db_session.commit()

    response = await client.get(f"/api/scenario/{scenario.id}")
    actions = {item["batchItemAction"] for item in response.json()["data"][0]["batchItems"]}

    assert "재배치" in actions
    assert "피킹" in actions
    assert "적재" in actions


# ══════════════════════════════════════════════════════════════════════
# DELETE /api/scenario/{scenario_id} — 시나리오 삭제
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_scenario_success(client: AsyncClient, db_session: AsyncSession):
    """시나리오 삭제 성공"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id)
    await db_session.commit()

    response = await client.delete(f"/api/scenario/{scenario.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert "삭제" in body["message"]

    # DB에서도 실제로 삭제됐는지 확인
    deleted = await db_session.get(Scenarios, scenario.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_delete_scenario_cascade(client: AsyncClient, db_session: AsyncSession):
    """시나리오 삭제 시 Batch, BatchItems, LazerCutting, EstimatedWips도 함께 삭제된다"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id)
    batch = await make_batch(db_session, scenario.id)

    loc_a = await make_location(db_session, "A-1")
    loc_b = await make_location(db_session, "B-1")
    wip = await make_wip(db_session)
    item = await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id)

    qr = QrCodes(qr_code="QR-CASCADE-TEST")
    db_session.add(qr)
    await db_session.flush()
    lc = LazerCutting(scenario_id=scenario.id, batch_id=batch.id, steel_wip_id=wip.id, estimated_cutting_time=30, status="PENDING")
    db_session.add(lc)
    await db_session.flush()
    ew = EstimatedWips(lazer_cutting_id=lc.id, qr_id=qr.id, material="SM355A", thickness=10.0, width=500.0, length=1000.0)
    db_session.add(ew)
    await db_session.commit()

    response = await client.delete(f"/api/scenario/{scenario.id}")
    assert response.status_code == 200

    # 연관 데이터 삭제 확인
    assert await db_session.get(Batch, batch.id) is None
    assert await db_session.get(BatchItems, item.id) is None
    assert await db_session.get(LazerCutting, lc.id) is None
    assert await db_session.get(EstimatedWips, ew.id) is None


@pytest.mark.asyncio
async def test_delete_scenario_not_found(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 시나리오 삭제 → 404"""
    response = await client.delete("/api/scenario/99999")

    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════
# GET /api/scenario_cart — DRAFT 시나리오 이력 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scenario_cart_empty(client: AsyncClient, db_session: AsyncSession):
    """DRAFT 시나리오가 없으면 빈 배열"""
    response = await client.get("/api/scenario_cart")

    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_scenario_cart_returns_draft_only(client: AsyncClient, db_session: AsyncSession):
    """status=DRAFT인 시나리오만 반환 (ORDERED, None은 제외)"""
    project = await make_project(db_session, title="포스코 건설")
    await make_scenario(db_session, project.id, status="DRAFT",   title="포스코 건설-1")
    await make_scenario(db_session, project.id, status="ORDERED", title="포스코 건설-1")
    await make_scenario(db_session, project.id, status=None,      title="포스코 건설-1")
    await db_session.commit()

    response = await client.get("/api/scenario_cart")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    # DRAFT 시나리오 1개 → 프로젝트 1개 그룹
    assert len(body["data"]) == 1
    assert len(body["data"][0]["scenario"]) == 1


@pytest.mark.asyncio
async def test_scenario_cart_multiple_projects(client: AsyncClient, db_session: AsyncSession):
    """여러 프로젝트의 DRAFT 시나리오가 프로젝트별로 그룹핑"""
    proj_a = await make_project(db_session, title="프로젝트A")
    proj_b = await make_project(db_session, title="프로젝트B")
    await make_scenario(db_session, proj_a.id, status="DRAFT", title="프로젝트A-1")
    await make_scenario(db_session, proj_b.id, status="DRAFT", title="프로젝트B-1")
    await db_session.commit()

    response = await client.get("/api/scenario_cart")

    data = response.json()["data"]
    assert len(data) == 2
    project_titles = {d["projectTitle"] for d in data}
    assert "프로젝트A" in project_titles
    assert "프로젝트B" in project_titles


@pytest.mark.asyncio
async def test_scenario_cart_filter_project_name(client: AsyncClient, db_session: AsyncSession):
    """projectName 부분 검색 필터"""
    proj_a = await make_project(db_session, title="포스코 프로젝트")
    proj_b = await make_project(db_session, title="현대 프로젝트")
    await make_scenario(db_session, proj_a.id, status="DRAFT", title="포스코 프로젝트-1")
    await make_scenario(db_session, proj_b.id, status="DRAFT", title="현대 프로젝트-1")
    await db_session.commit()

    response = await client.get("/api/scenario_cart", params={"projectName": "포스코"})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["projectTitle"] == "포스코 프로젝트"


@pytest.mark.asyncio
async def test_scenario_cart_statistics_fields(client: AsyncClient, db_session: AsyncSession):
    """
    응답에 selectedWips, #relocation, #crane, totalMinute 필드가 포함된다.
    PICKING 2개, RELOCATE 1개인 시나리오 기준.
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    batch = await make_batch(db_session, scenario.id)

    loc_a = await make_location(db_session, "A-1")
    loc_b = await make_location(db_session, "B-1")
    wip = await make_wip(db_session)

    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="PICKING",  item_order=1)
    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="PICKING",  item_order=2)
    await make_batch_item(db_session, batch.id, wip.id, loc_a.id, loc_b.id, action="RELOCATE", item_order=3)
    await db_session.commit()

    response = await client.get("/api/scenario_cart")
    scen = response.json()["data"][0]["scenario"][0]

    assert scen["selectedWips"] == 2
    assert scen["#relocation"] == 1
    assert scen["#crane"] == 3    # selectedWips + num_relocation
    assert "totalMinute" in scen


# ══════════════════════════════════════════════════════════════════════
# GET /api/scenario_send/ — 현장 전송 이력 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scenario_send_history_empty(client: AsyncClient, db_session: AsyncSession):
    """전송된 시나리오 없을 때 빈 배열"""
    response = await client.get("/api/scenario_send/")

    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_scenario_send_history_excludes_draft_and_null(
    client: AsyncClient, db_session: AsyncSession
):
    """DRAFT 및 status=None 시나리오는 전송 이력에 포함되지 않는다"""
    project = await make_project(db_session)
    await make_scenario(db_session, project.id, status=None)
    await make_scenario(db_session, project.id, status="DRAFT", title="테스트 시나리오-2")
    await db_session.commit()

    response = await client.get("/api/scenario_send/")

    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_scenario_send_history_with_ordered(client: AsyncClient, db_session: AsyncSession):
    """ORDERED 시나리오가 전송 이력에 반환된다"""
    project = await make_project(db_session, title="현장 전송 프로젝트")
    scenario = await make_scenario(
        db_session, project.id, status="ORDERED", title="현장 전송 프로젝트-1"
    )
    scenario.ordered_at = datetime(2026, 3, 1, 9, 0, 0)
    await db_session.commit()

    response = await client.get("/api/scenario_send/")

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["projectTitle"] == "현장 전송 프로젝트"
    assert len(data[0]["scenarios"]) == 1
    assert data[0]["scenarios"][0]["scenarioTitle"] == "현장 전송 프로젝트-1"


@pytest.mark.asyncio
async def test_scenario_send_history_filter_project_name(
    client: AsyncClient, db_session: AsyncSession
):
    """projectName 필터로 특정 프로젝트 이력만 조회"""
    proj_a = await make_project(db_session, title="포스코 건설")
    proj_b = await make_project(db_session, title="롯데 건설")
    scen_a = await make_scenario(db_session, proj_a.id, status="ORDERED", title="포스코 건설-1")
    scen_b = await make_scenario(db_session, proj_b.id, status="ORDERED", title="롯데 건설-1")
    scen_a.ordered_at = datetime(2026, 3, 1)
    scen_b.ordered_at = datetime(2026, 3, 2)
    await db_session.commit()

    response = await client.get("/api/scenario_send/", params={"projectName": "포스코"})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["projectTitle"] == "포스코 건설"


# ══════════════════════════════════════════════════════════════════════
# POST /api/scenario_send/{scenario_id} — 시나리오 현장 전송
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_send_scenario_success(client: AsyncClient, db_session: AsyncSession):
    """
    DRAFT 시나리오를 현장 전송하면:
    - 시나리오 status → ORDERED
    - BatchItems status → PENDING
    - PICKING 대상 WIP status → RESERVATED
    """
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    batch = await make_batch(db_session, scenario.id)

    loc_a = await make_location(db_session, "A-1")
    loc_b = await make_location(db_session, "B-1")
    wip = await make_wip(db_session, status="IN_STOCK", location_id=loc_a.id)

    picking_item = await make_batch_item(
        db_session, batch.id, wip.id, loc_a.id, loc_b.id,
        action="PICKING", status="BEFORE_PENDING", item_order=1,
    )
    reloc_item = await make_batch_item(
        db_session, batch.id, wip.id, loc_a.id, loc_b.id,
        action="RELOCATE", status="BEFORE_PENDING", item_order=2,
    )
    await db_session.commit()

    response = await client.post(f"/api/scenario_send/{scenario.id}")

    assert response.status_code == 200
    assert response.json()["status"] == 200

    # 시나리오 상태 확인
    await db_session.refresh(scenario)
    assert scenario.status == "ORDERED"
    assert scenario.ordered_at is not None

    # BatchItems 상태 확인
    await db_session.refresh(picking_item)
    await db_session.refresh(reloc_item)
    assert picking_item.status == "PENDING"
    assert reloc_item.status == "PENDING"

    # PICKING 대상 WIP 상태 확인
    await db_session.refresh(wip)
    assert wip.status == "RESERVATED"


@pytest.mark.asyncio
async def test_send_scenario_no_batch_items(client: AsyncClient, db_session: AsyncSession):
    """Batch나 BatchItems가 없어도 전송 성공 (상태만 ORDERED로 변경)"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="DRAFT")
    await db_session.commit()

    response = await client.post(f"/api/scenario_send/{scenario.id}")

    assert response.status_code == 200
    await db_session.refresh(scenario)
    assert scenario.status == "ORDERED"


@pytest.mark.asyncio
async def test_send_scenario_not_draft(client: AsyncClient, db_session: AsyncSession):
    """DRAFT가 아닌 시나리오 전송 시도 → 400"""
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id, status="ORDERED")
    await db_session.commit()

    response = await client.post(f"/api/scenario_send/{scenario.id}")

    assert response.status_code == 400
    assert "DRAFT" in response.json()["message"]


@pytest.mark.asyncio
async def test_send_scenario_not_found(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 시나리오 전송 → 400"""
    response = await client.post("/api/scenario_send/99999")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_scenario_deletes_competing_scenarios(
    client: AsyncClient, db_session: AsyncSession
):
    """
    동일 title을 가진 DRAFT/None 비교군 시나리오들은 전송 시 함께 삭제된다.
    """
    project = await make_project(db_session)
    # 전송 대상 (DRAFT)
    target = await make_scenario(db_session, project.id, status="DRAFT", title="공통 시나리오")
    # 비교군 (같은 title, DRAFT)
    rival1 = await make_scenario(db_session, project.id, status="DRAFT", title="공통 시나리오")
    # 비교군 (같은 title, None)
    rival2 = await make_scenario(db_session, project.id, status=None, title="공통 시나리오")
    await db_session.commit()

    response = await client.post(f"/api/scenario_send/{target.id}")

    assert response.status_code == 200

    # rival1, rival2는 삭제되어야 함
    assert await db_session.get(Scenarios, rival1.id) is None
    assert await db_session.get(Scenarios, rival2.id) is None

    # target은 ORDERED로 남아 있어야 함
    await db_session.refresh(target)
    assert target.status == "ORDERED"


@pytest.mark.asyncio
async def test_send_scenario_emergency_order(client: AsyncClient, db_session: AsyncSession):
    """
    긴급 발주(emergency_or_not=True)는 scenario_order=0을 부여받는다.
    """
    project = await make_project(db_session)
    normal_scen = await make_scenario(db_session, project.id, status="ORDERED", order=0)
    emergency_scen = await make_scenario(
        db_session, project.id, status="DRAFT", title="테스트 시나리오-2", emergency=True
    )
    await db_session.commit()

    response = await client.post(f"/api/scenario_send/{emergency_scen.id}")

    assert response.status_code == 200
    await db_session.refresh(emergency_scen)
    await db_session.refresh(normal_scen)
    # 긴급 시나리오는 0순위
    assert emergency_scen.scenario_order == 0
    # 기존 ORDERED 시나리오 순서는 +1 밀림
    assert normal_scen.scenario_order == 1
