# tests/test_field.py
"""
현장 담당자 API 테스트
  - GET /api/field/end      (작업 완료 화면)
  - GET /api/field/progress (생산 중 화면)

[픽스처 규칙]
  - MySQL server_default(now() 등)는 SQLite에서 동작하지 않으므로
    created_at, status 등 모든 컬럼 값을 픽스처에서 직접 지정한다.
  - 각 테스트는 conftest의 db_session/client를 공유하지 않으므로 독립적이다.
"""

from datetime import date, datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.models import Locations, SteelWip, Scenarios, Batch, BatchItems, LazerCutting, EstimatedWips, QrCodes


# ══════════════════════════════════════════════════════════════════════
# 테스트 데이터 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════

async def make_location(db: AsyncSession, loc_name: str = "A-1") -> Locations:
    loc = Locations(loc_name=loc_name, loc_can_stock=1, loc_stack_height=10)
    db.add(loc)
    await db.flush()
    return loc


async def make_wip(
    db: AsyncSession,
    location_id: int,
    material: str = "SM355A",
    stack_level: int = 1,
) -> SteelWip:
    wip = SteelWip(
        status="IN_STOCK",
        material=material,
        thickness=20.0,
        width=2438.0,
        length=6096.0,
        weight=100.0,
        manufacturer="POSCO",
        location_id=location_id,
        stack_level=stack_level,
    )
    db.add(wip)
    await db.flush()
    return wip


async def make_scenario(db: AsyncSession, order: int = 0) -> Scenarios:
    scenario = Scenarios(
        title="테스트 시나리오-1",
        status="ORDERED",
        scenario_due=date(2026, 3, 31),
        scenario_order=order,
        lazer_name="LAZER1",
        emergency_or_not=False,
        created_at=datetime.now(),   # server_default 우회
    )
    db.add(scenario)
    await db.flush()
    return scenario


async def make_batch(db: AsyncSession, scenario_id: int, batch_order: int = 1) -> Batch:
    batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
    db.add(batch)
    await db.flush()
    return batch


async def make_batch_item(
    db: AsyncSession,
    batch_id: int,
    wip_id: int,
    from_loc_id: int,
    to_loc_id: int,
    action: str = "PICKING",
    status: str = "COMPLETED",
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
        expected_start_time=10,
        expected_running_time=5,
    )
    db.add(item)
    await db.flush()
    return item


# ══════════════════════════════════════════════════════════════════════
# 테스트 케이스
# ══════════════════════════════════════════════════════════════════════

async def test_end_no_current_scenario(client: AsyncClient, db_session: AsyncSession):
    """
    scenario_order==0인 시나리오가 없으면 data는 빈 배열이다.
    """
    response = await client.get("/api/field/end", params={"batchId": 999})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["message"] == "현장 생산 완료 정보 조회에 성공했습니다."
    assert body["data"] == []


async def test_end_batch_not_in_current_scenario(client: AsyncClient, db_session: AsyncSession):
    """
    batchId가 현재 시나리오(scenario_order==0)에 속하지 않으면 빈 배열이다.
    다른 시나리오(order=1)의 배치를 넘기는 상황을 검증한다.
    """
    # 현재 시나리오 (order=0) — 배치 없음
    await make_scenario(db_session, order=0)

    # 다음 시나리오 (order=1) — 여기에 배치가 있음
    other_scenario = await make_scenario(db_session, order=1)
    other_batch = await make_batch(db_session, scenario_id=other_scenario.id)
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": other_batch.id})

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_end_no_completed_batches(client: AsyncClient, db_session: AsyncSession):
    """
    현재 시나리오에 배치가 있지만 아무것도 완료되지 않은 경우,
    batch 목록은 비어 있고 진행률은 0.0이다.
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "B-1")
    wip = await make_wip(db_session, loc1.id)
    scenario = await make_scenario(db_session, order=0)
    batch = await make_batch(db_session, scenario.id)
    await make_batch_item(
        db_session, batch.id, wip.id, loc1.id, loc2.id,
        action="PICKING", status="PENDING",
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch.id})

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["scenarioProgressRate"] == 0.0
    assert data["batch"] == []


async def test_end_with_completed_picking_batch(client: AsyncClient, db_session: AsyncSession):
    """
    PICKING 아이템이 COMPLETED인 배치 → picking 배열에 정상 포함.
    wipId, material, fromLocationName, toLocationName 검증.
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "B-1")
    wip = await make_wip(db_session, loc1.id, material="SM355A")
    scenario = await make_scenario(db_session, order=0)
    batch = await make_batch(db_session, scenario.id)
    await make_batch_item(
        db_session, batch.id, wip.id, loc1.id, loc2.id,
        action="PICKING", status="COMPLETED",
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch.id})

    assert response.status_code == 200
    data = response.json()["data"][0]

    # 시나리오 메타
    assert data["scenarioId"] == scenario.id
    assert data["scenarioTitle"] == "테스트 시나리오-1"
    assert data["scenarioProgressRate"] == 1.0

    # 배치 내용
    assert len(data["batch"]) == 1
    picking = data["batch"][0]["picking"]
    assert len(picking) == 1
    assert picking[0]["wipId"] == wip.id
    assert picking[0]["material"] == "SM355A"
    assert picking[0]["fromLocationName"] == "A-1"
    assert picking[0]["toLocationName"] == "B-1"


async def test_end_with_completed_relocate_batch(client: AsyncClient, db_session: AsyncSession):
    """
    RELOCATE 아이템이 COMPLETED인 배치 → relocation 배열에 정상 포함.
    expectedRunningTime도 검증한다.
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "C-1")
    wip = await make_wip(db_session, loc1.id, material="SS275")
    scenario = await make_scenario(db_session, order=0)
    batch = await make_batch(db_session, scenario.id)
    await make_batch_item(
        db_session, batch.id, wip.id, loc1.id, loc2.id,
        action="RELOCATE", status="COMPLETED",
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch.id})

    assert response.status_code == 200
    batch_data = response.json()["data"][0]["batch"][0]

    relocation = batch_data["relocation"]
    assert len(relocation) == 1
    assert relocation[0]["material"] == "SS275"
    assert relocation[0]["fromLocationName"] == "A-1"
    assert relocation[0]["toLocationName"] == "C-1"
    assert relocation[0]["expectedRunningTime"] == 5

    # RELOCATE 배치이므로 picking은 비어 있어야 함
    assert batch_data["picking"] == []


async def test_end_partial_completion_progress_rate(client: AsyncClient, db_session: AsyncSession):
    """
    배치 2개 중 1개만 완료 →
      - scenarioProgressRate == 0.5
      - batch 목록에는 완료된 배치(batch1)만 포함
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "B-1")
    wip1 = await make_wip(db_session, loc1.id)
    wip2 = await make_wip(db_session, loc2.id)

    scenario = await make_scenario(db_session, order=0)
    batch1 = await make_batch(db_session, scenario.id, batch_order=1)
    batch2 = await make_batch(db_session, scenario.id, batch_order=2)

    # batch1 — COMPLETED
    await make_batch_item(
        db_session, batch1.id, wip1.id, loc1.id, loc2.id,
        action="PICKING", status="COMPLETED", item_order=1,
    )
    # batch2 — PENDING (미완료)
    await make_batch_item(
        db_session, batch2.id, wip2.id, loc2.id, loc1.id,
        action="RELOCATE", status="PENDING", item_order=1,
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch1.id})

    assert response.status_code == 200
    data = response.json()["data"][0]

    assert data["scenarioProgressRate"] == 0.5
    assert len(data["batch"]) == 1                        # 완료된 batch1만
    assert data["batch"][0]["picking"][0]["wipId"] == wip1.id


async def test_end_mixed_items_in_one_batch(client: AsyncClient, db_session: AsyncSession):
    """
    한 배치에 RELOCATE + PICKING 아이템이 섞여 있을 때
    각각 올바른 배열에 분리되어 반환된다.
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "B-1")
    loc3 = await make_location(db_session, "LAZER1")
    wip1 = await make_wip(db_session, loc1.id, material="SS275")   # 재배치 대상
    wip2 = await make_wip(db_session, loc2.id, material="SM355A")  # 피킹 대상

    scenario = await make_scenario(db_session, order=0)
    batch = await make_batch(db_session, scenario.id)

    await make_batch_item(
        db_session, batch.id, wip1.id, loc1.id, loc2.id,
        action="RELOCATE", status="COMPLETED", item_order=1,
    )
    await make_batch_item(
        db_session, batch.id, wip2.id, loc2.id, loc3.id,
        action="PICKING", status="COMPLETED", item_order=2,
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch.id})

    assert response.status_code == 200
    batch_data = response.json()["data"][0]["batch"][0]

    assert len(batch_data["relocation"]) == 1
    assert batch_data["relocation"][0]["material"] == "SS275"

    assert len(batch_data["picking"]) == 1
    assert batch_data["picking"][0]["material"] == "SM355A"
    assert batch_data["picking"][0]["toLocationName"] == "LAZER1"


async def test_end_batch_partially_completed_not_shown(client: AsyncClient, db_session: AsyncSession):
    """
    배치 내 아이템이 일부만 COMPLETED인 경우 해당 배치는 batch 목록에서 제외된다.
    (완료 기준: 배치 내 모든 아이템이 COMPLETED)
    """
    loc1 = await make_location(db_session, "A-1")
    loc2 = await make_location(db_session, "B-1")
    wip1 = await make_wip(db_session, loc1.id)
    wip2 = await make_wip(db_session, loc2.id)

    scenario = await make_scenario(db_session, order=0)
    batch = await make_batch(db_session, scenario.id)

    await make_batch_item(
        db_session, batch.id, wip1.id, loc1.id, loc2.id,
        action="PICKING", status="COMPLETED", item_order=1,
    )
    await make_batch_item(
        db_session, batch.id, wip2.id, loc2.id, loc1.id,
        action="RELOCATE", status="IN_PROGRESS", item_order=2,  # 아직 진행 중
    )
    await db_session.commit()

    response = await client.get("/api/field/end", params={"batchId": batch.id})

    assert response.status_code == 200
    data = response.json()["data"][0]
    # IN_PROGRESS 아이템이 있으므로 이 배치는 완료 배치로 포함되지 않음
    assert data["batch"] == []


# ══════════════════════════════════════════════════════════════════════
# 통합 테스트 — capstoneDB-backup 실 dump 데이터 기반
#
# [dump 데이터 요약 — 2026-04-05 업데이트]
#   scenarios
#     id=1  title='포스코 건설(80톤)-1'    scenario_order=1  status='ORDERED'  ← 현재 활성 (최소 order)
#     id=2  title='토네이도 건설(12톤)-1'  scenario_order=2  status='ORDERED'  ← 대기 중
#   batch
#     id=1,2,3 → scenario_id=1 (배치 1: 30개 항목, 배치 2: 19개, 배치 3: 20개 → 총 69개)
#     id=4,5,6 → scenario_id=2 (배치 4: 36개 항목, 배치 5: 30개, 배치 6: 15개 → 총 81개)
#   batch_items
#     모든 status = 'PENDING' (COMPLETED 없음)
#     batch 1 아이템: id=1~30
#       - PICKING 4개: id=3(from=3:A-3,to=15:S4-1), id=7, id=18, id=28
#       - RELOCATE/INBOUND: 나머지
#   locations
#     id=1  'A-1',  id=2  'A-2',  id=3  'A-3',  id=4  'A-4'
#     id=5  'B-1',  id=6  'B-2',  id=7  'B-3'
#     id=8  'C-1',  id=9  'C-2'
#     id=15 'S4-1', id=16 'S4-2', id=17 'S4-3', id=18 'S4-4'
#     id=23 'ETC'
# ══════════════════════════════════════════════════════════════════════

async def test_integration_end_no_completed_batches(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 모든 batch_item이 PENDING이므로
    완료된 배치가 없어 batch 목록은 비어 있어야 한다.
    진행률도 0.0이어야 한다.

    시나리오 1(ORDERED, scenario_order=1 = 현재 활성), 배치 1(batch_id=1) 사용.
    """
    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200

    data = body["data"][0]
    assert data["scenarioId"] == 1
    assert data["scenarioProgressRate"] == 0.0
    assert data["batch"] == []


async def test_integration_end_scenario_meta(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 시나리오 메타 정보(제목 등)가 올바르게 반환된다.
    현재 활성 시나리오(scenario_order=1)의 제목 확인.
    """
    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["scenarioTitle"] == "포스코 건설(80톤)-1"


async def test_integration_end_batch_from_other_scenario_rejected(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 다른 시나리오(id=2)에 속하는 배치(id=4)를
    전달하면 현재 시나리오(id=1, scenario_order=1)에 없으므로 빈 배열을 반환해야 한다.
    """
    # batch_id=4 는 scenario_id=2 소속 (현재 활성 시나리오 1과 다름)
    response = await client_with_dump.get("/api/field/end", params={"batchId": 4})

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_integration_end_completed_after_update(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 배치 1의 아이템을 모두 COMPLETED로 직접 업데이트 후
    호출하면 배치 1이 결과에 포함되고, 진행률이 0보다 커야 한다.

    배치 1 아이템: id=1~30 (총 30개)
    그 중 PICKING: id=3, 7, 18, 28 (4개)
    """
    # 배치 1의 모든 아이템을 COMPLETED로 업데이트 (실 DB 상태를 테스트 내에서 조작)
    await db_with_dump.execute(
        text("UPDATE batch_items SET status='COMPLETED' WHERE batch_id=1")
    )
    await db_with_dump.commit()

    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    assert response.status_code == 200
    data = response.json()["data"][0]

    # 배치 1이 완료 배치로 포함되어야 함
    assert len(data["batch"]) == 1

    # 진행률: 배치1(30개) COMPLETED / 시나리오1 전체(배치1+2+3) 아이템 수
    # 배치 2: 아이템 19개, 배치 3: 아이템 20개
    total_items = 30 + 19 + 20   # 69
    completed_items = 30
    expected_rate = round(completed_items / total_items, 2)
    assert data["scenarioProgressRate"] == expected_rate

    # picking, relocation 배열이 각각 올바르게 채워졌는지 확인
    batch_data = data["batch"][0]
    assert len(batch_data["picking"]) == 4     # 배치 1의 PICKING 아이템 수
    assert len(batch_data["relocation"]) > 0   # RELOCATE 아이템 존재


async def test_integration_end_location_names_resolved(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — location ID가 실제 loc_name으로 변환되는지 검증.
    배치 1, 아이템 3 (PICKING): from_location=3('A-3'), to_location=15('S4-1')
    """
    await db_with_dump.execute(
        text("UPDATE batch_items SET status='COMPLETED' WHERE batch_id=1")
    )
    await db_with_dump.commit()

    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    picking_items = response.json()["data"][0]["batch"][0]["picking"]
    # batch_item id=3 (PICKING): from_location=3(A-3), to_location=15(S4-1), steel_wip_id=42
    item_3 = next(p for p in picking_items if p["batchItemId"] == 3)
    assert item_3["fromLocationName"] == "A-3"
    assert item_3["toLocationName"] == "S4-1"


# ══════════════════════════════════════════════════════════════════════
# 생산 중 화면 (GET /api/field/progress) — 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════

async def make_qr_code(db: AsyncSession, qr_code: str = "QR-TEST") -> QrCodes:
    qr = QrCodes(qr_code=qr_code)
    db.add(qr)
    await db.flush()
    return qr


async def make_lazer_cutting(
    db: AsyncSession,
    batch_id: int,
    steel_wip_id: int | None = None,
    ec_time: int = 10,
) -> LazerCutting:
    lc = LazerCutting(
        batch_id=batch_id,
        steel_wip_id=steel_wip_id,
        estimated_cutting_time=ec_time,
        status="PENDING",
    )
    db.add(lc)
    await db.flush()
    return lc


async def make_estimated_wip(
    db: AsyncSession,
    lazer_cutting_id: int,
    qr_id: int,
    material: str = "SM355A",
    thickness: float = 20.0,
    width: float = 1000.0,
    length: float = 2000.0,
) -> EstimatedWips:
    ew = EstimatedWips(
        lazer_cutting_id=lazer_cutting_id,
        qr_id=qr_id,
        material=material,
        thickness=thickness,
        width=width,
        length=length,
    )
    db.add(ew)
    await db.flush()
    return ew


# ══════════════════════════════════════════════════════════════════════
# 생산 중 화면 — 단위 테스트
# ══════════════════════════════════════════════════════════════════════

async def test_progress_no_scenario(client: AsyncClient, db_session: AsyncSession):
    """
    시나리오가 없으면 data는 빈 배열이다.
    """
    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["message"] == "현장 생산 중 정보 조회에 성공했습니다."
    assert body["data"] == []


async def test_progress_no_batch(client: AsyncClient, db_session: AsyncSession):
    """
    시나리오는 있지만 배치가 없으면 빈 배열이다.
    """
    await make_scenario(db_session, order=1)
    await db_session.commit()

    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_progress_no_lazer_cutting(client: AsyncClient, db_session: AsyncSession):
    """
    시나리오와 배치는 있지만 lazer_cutting이 없으면 빈 배열이다.
    """
    scenario = await make_scenario(db_session, order=1)
    await make_batch(db_session, scenario.id, batch_order=1)
    await db_session.commit()

    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_progress_returns_cutting_data(client: AsyncClient, db_session: AsyncSession):
    """
    lazer_cutting 1개에 estimated_wip 1개가 연결된 경우
    expectedTotalRunningTime, lazer_cutting 배열, wip 목록이 정상 반환된다.
    """
    loc_from = await make_location(db_session, "A-1")
    loc_to   = await make_location(db_session, "B-1")
    input_wip = await make_wip(db_session, loc_from.id, material="SM355A")

    # 예상 재공품을 위한 QR 코드 + SteelWip
    qr = await make_qr_code(db_session, "QR-UNIT-1")
    result_wip = SteelWip(
        status="REGISTERED",
        material="SM355A",
        thickness=20.0,
        width=1000.0,
        length=2000.0,
        weight=50.0,
        manufacturer="POSCO",
        location_id=None,
        qr_id=qr.id,
    )
    db_session.add(result_wip)
    await db_session.flush()

    scenario = await make_scenario(db_session, order=1)
    batch    = await make_batch(db_session, scenario.id, batch_order=1)
    lc       = await make_lazer_cutting(db_session, batch.id, steel_wip_id=input_wip.id, ec_time=30)
    await make_estimated_wip(db_session, lc.id, qr.id, thickness=20.0, width=1000.0, length=2000.0)

    # INBOUND batch_item: 절단 후 result_wip를 loc_to에 적재
    await make_batch_item(
        db_session, batch.id, result_wip.id, None, loc_to.id,
        action="INBOUND", status="PENDING",
    )
    await db_session.commit()

    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    data = response.json()["data"][0]

    assert data["expectedTotalRunningTime"] == 30
    assert len(data["lazer_cutting"]) == 1

    lc_data = data["lazer_cutting"][0]
    assert lc_data["inputWipId"] == input_wip.id
    assert lc_data["material"] == "SM355A"
    assert len(lc_data["wip"]) == 1

    wip_item = lc_data["wip"][0]
    assert wip_item["wipId"] == result_wip.id
    assert wip_item["wipStatus"] == "REGISTERED"
    assert wip_item["toLocation"] == "B-1"


async def test_progress_wip_name_format(client: AsyncClient, db_session: AsyncSession):
    """
    wipName은 "{두께}X{가로}X{세로}" 형식이다.
    - 정수는 소수점 없이 반환 (예: 20 → "20", 1000 → "1000")
    - 소수가 있으면 그대로 반환 (예: 1446.4 → "1446.4")
    """
    loc = await make_location(db_session, "A-1")
    loc_to = await make_location(db_session, "B-1")

    qr = await make_qr_code(db_session, "QR-NAME-1")
    # 두께=16(정수), 가로=1446.4(소수), 세로=1511(정수) → "16X1446.4X1511"
    result_wip = SteelWip(
        status="REGISTERED",
        material="SM355A",
        thickness=16.0,
        width=1446.4,
        length=1511.0,
        weight=274.5,
        manufacturer="POSCO",
        location_id=None,
        qr_id=qr.id,
    )
    db_session.add(result_wip)
    await db_session.flush()

    scenario = await make_scenario(db_session, order=1)
    batch    = await make_batch(db_session, scenario.id, batch_order=1)
    input_wip = await make_wip(db_session, loc.id)
    lc = await make_lazer_cutting(db_session, batch.id, steel_wip_id=input_wip.id, ec_time=10)
    await make_estimated_wip(db_session, lc.id, qr.id, thickness=16.0, width=1446.4, length=1511.0)
    await make_batch_item(
        db_session, batch.id, result_wip.id, None, loc_to.id,
        action="INBOUND", status="PENDING",
    )
    await db_session.commit()

    response = await client.get("/api/field/progress")

    wip_item = response.json()["data"][0]["lazer_cutting"][0]["wip"][0]
    assert wip_item["wipName"] == "16X1446.4X1511"


async def test_progress_wip_status_mapping(client: AsyncClient, db_session: AsyncSession):
    """
    INBOUND batch_item 상태에 따른 status 문자열 변환 검증.
      COMPLETED  → "적재 완료"
      IN_PROGRESS → "적재 대기"
      PENDING     → "PENDING" (원본 그대로)
    """
    loc = await make_location(db_session, "A-1")
    loc_to1 = await make_location(db_session, "B-1")
    loc_to2 = await make_location(db_session, "B-2")
    loc_to3 = await make_location(db_session, "B-3")

    qr1 = await make_qr_code(db_session, "QR-S1")
    qr2 = await make_qr_code(db_session, "QR-S2")
    qr3 = await make_qr_code(db_session, "QR-S3")

    def new_wip(qr_id, loc_id):
        return SteelWip(
            status="REGISTERED", material="SM355A",
            thickness=10.0, width=500.0, length=1000.0,
            weight=10.0, manufacturer="POSCO",
            location_id=loc_id, qr_id=qr_id,
        )

    wip1 = new_wip(qr1.id, None)
    wip2 = new_wip(qr2.id, None)
    wip3 = new_wip(qr3.id, None)
    db_session.add_all([wip1, wip2, wip3])
    await db_session.flush()

    scenario  = await make_scenario(db_session, order=1)
    batch     = await make_batch(db_session, scenario.id, batch_order=1)
    input_wip = await make_wip(db_session, loc.id)
    lc = await make_lazer_cutting(db_session, batch.id, steel_wip_id=input_wip.id, ec_time=5)

    await make_estimated_wip(db_session, lc.id, qr1.id)
    await make_estimated_wip(db_session, lc.id, qr2.id)
    await make_estimated_wip(db_session, lc.id, qr3.id)

    # INBOUND items — 상태 각각 다르게 설정
    await make_batch_item(db_session, batch.id, wip1.id, None, loc_to1.id,
                          action="INBOUND", status="COMPLETED")
    await make_batch_item(db_session, batch.id, wip2.id, None, loc_to2.id,
                          action="INBOUND", status="IN_PROGRESS")
    await make_batch_item(db_session, batch.id, wip3.id, None, loc_to3.id,
                          action="INBOUND", status="PENDING")
    await db_session.commit()

    response = await client.get("/api/field/progress")
    wip_list = response.json()["data"][0]["lazer_cutting"][0]["wip"]

    # wipId 기준으로 정렬해서 검증
    wip_map = {w["wipId"]: w["status"] for w in wip_list}
    assert wip_map[wip1.id] == "적재 완료"
    assert wip_map[wip2.id] == "적재 대기"
    assert wip_map[wip3.id] == "PENDING"


async def test_progress_lazer_cutting_without_estimated_wips(
    client: AsyncClient, db_session: AsyncSession
):
    """
    lazer_cutting에 estimated_wip이 없는 경우 wip 배열이 비어 있다.
    expectedTotalRunningTime은 정상적으로 합산된다.
    """
    loc = await make_location(db_session, "A-1")
    input_wip = await make_wip(db_session, loc.id)

    scenario = await make_scenario(db_session, order=1)
    batch    = await make_batch(db_session, scenario.id, batch_order=1)
    await make_lazer_cutting(db_session, batch.id, steel_wip_id=input_wip.id, ec_time=43)
    await db_session.commit()

    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["expectedTotalRunningTime"] == 43
    assert len(data["lazer_cutting"]) == 1
    assert data["lazer_cutting"][0]["wip"] == []


async def test_progress_total_time_sum(client: AsyncClient, db_session: AsyncSession):
    """
    lazer_cutting 3개의 estimated_cutting_time이 올바르게 합산된다.
    10 + 20 + 30 = 60분
    """
    loc = await make_location(db_session, "A-1")
    wip1 = await make_wip(db_session, loc.id)
    wip2 = await make_wip(db_session, loc.id)
    wip3 = await make_wip(db_session, loc.id)

    scenario = await make_scenario(db_session, order=1)
    batch    = await make_batch(db_session, scenario.id, batch_order=1)
    await make_lazer_cutting(db_session, batch.id, steel_wip_id=wip1.id, ec_time=10)
    await make_lazer_cutting(db_session, batch.id, steel_wip_id=wip2.id, ec_time=20)
    await make_lazer_cutting(db_session, batch.id, steel_wip_id=wip3.id, ec_time=30)
    await db_session.commit()

    response = await client.get("/api/field/progress")

    assert response.status_code == 200
    assert response.json()["data"][0]["expectedTotalRunningTime"] == 60


# ══════════════════════════════════════════════════════════════════════
# 생산 중 화면 — 통합 테스트 (capstoneDB-backup 실 dump 데이터)
#
# [dump 데이터 요약 — 생산 중 화면 관련]
#   현재 활성 시나리오: id=1 (scenario_order=1)
#   현재 배치: id=1 (batch_order=1, scenario_id=1)
#   lazer_cutting (batch_id=1):
#     id=1  steel_wip_id=42  ec_time=23  → estimated_wips: qr_id=103(wip_id=103), qr_id=104(wip_id=104)
#     id=2  steel_wip_id=1   ec_time=93  → estimated_wips: qr_id=105(wip_id=105), qr_id=106(wip_id=106)
#     id=3  steel_wip_id=4   ec_time=32  → estimated_wips: qr_id=107(wip_id=107), qr_id=108(wip_id=108)
#     id=4  steel_wip_id=39  ec_time=43  → estimated_wips: 없음
#   예상 총 소요 시간: 23+93+32+43 = 191분
#   INBOUND batch_items (batch_id=1):
#     wip_id=103 → to_location=8(C-1),  status=PENDING
#     wip_id=104 → to_location=1(A-1),  status=PENDING
#     wip_id=105 → to_location=4(A-4),  status=PENDING
#     wip_id=106 → to_location=7(B-3),  status=PENDING
#     wip_id=107 → to_location=8(C-1),  status=PENDING
#     wip_id=108 → to_location=3(A-3),  status=PENDING
# ══════════════════════════════════════════════════════════════════════

async def test_integration_progress_expected_total_time(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — batch 1의 모든 lazer_cutting
    estimated_cutting_time 합산이 191분이어야 한다.
    (23 + 93 + 32 + 43 = 191)
    """
    response = await client_with_dump.get("/api/field/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["data"][0]["expectedTotalRunningTime"] == 191


async def test_integration_progress_lazer_cutting_count(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — batch 1의 lazer_cutting이 4개여야 한다.
    """
    response = await client_with_dump.get("/api/field/progress")

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert len(data["lazer_cutting"]) == 4


async def test_integration_progress_estimated_wip_count(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — estimated_wips 개수 검증.
      lc_id=1: 2개, lc_id=2: 2개, lc_id=3: 2개, lc_id=4: 0개
    """
    response = await client_with_dump.get("/api/field/progress")

    assert response.status_code == 200
    lc_list = response.json()["data"][0]["lazer_cutting"]

    # lazerCuttingId 기준으로 매핑
    lc_map = {lc["lazerCuttingId"]: lc for lc in lc_list}
    assert len(lc_map[1]["wip"]) == 2
    assert len(lc_map[2]["wip"]) == 2
    assert len(lc_map[3]["wip"]) == 2
    assert len(lc_map[4]["wip"]) == 0


async def test_integration_progress_wip_location_resolved(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — INBOUND batch_item의 to_location이
    실제 loc_name으로 변환되는지 검증.

    lc_id=1의 첫 번째 예상 재공품(wip_id=103):
      INBOUND batch_item → to_location=8 → 'C-1'
    """
    response = await client_with_dump.get("/api/field/progress")

    assert response.status_code == 200
    lc_list = response.json()["data"][0]["lazer_cutting"]

    lc1 = next(lc for lc in lc_list if lc["lazerCuttingId"] == 1)
    # wip_id=103의 toLocation이 'C-1'인지 확인
    wip_103 = next(w for w in lc1["wip"] if w["wipId"] == 103)
    assert wip_103["toLocation"] == "C-1"


async def test_integration_progress_wip_name_format(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — wipName 형식 검증.

    wip_id=103: thickness=16, width=1446.4, length=1511
    → "16X1446.4X1511"   (정수는 소수점 제거, 소수는 유지)
    """
    response = await client_with_dump.get("/api/field/progress")

    lc_list = response.json()["data"][0]["lazer_cutting"]
    lc1 = next(lc for lc in lc_list if lc["lazerCuttingId"] == 1)
    wip_103 = next(w for w in lc1["wip"] if w["wipId"] == 103)

    assert wip_103["wipName"] == "16X1446.4X1511"


async def test_integration_progress_status_after_update(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — INBOUND batch_item 상태를 직접 변경 후
    status 문자열이 올바르게 변환되는지 검증.

    wip_id=103 INBOUND(id=8):  PENDING → COMPLETED → "적재 완료"
    wip_id=104 INBOUND(id=9):  PENDING → IN_PROGRESS → "적재 대기"
    wip_id=107 INBOUND(id=26): PENDING 그대로 → "PENDING"
    """
    await db_with_dump.execute(
        text("UPDATE batch_items SET status='COMPLETED' WHERE id=8")
    )
    await db_with_dump.execute(
        text("UPDATE batch_items SET status='IN_PROGRESS' WHERE id=9")
    )
    await db_with_dump.commit()

    response = await client_with_dump.get("/api/field/progress")

    lc_list = response.json()["data"][0]["lazer_cutting"]
    lc1 = next(lc for lc in lc_list if lc["lazerCuttingId"] == 1)

    wip_map = {w["wipId"]: w["status"] for w in lc1["wip"]}
    assert wip_map[103] == "적재 완료"
    assert wip_map[104] == "적재 대기"

    lc3 = next(lc for lc in lc_list if lc["lazerCuttingId"] == 3)
    wip_map3 = {w["wipId"]: w["status"] for w in lc3["wip"]}
    assert wip_map3[107] == "PENDING"
