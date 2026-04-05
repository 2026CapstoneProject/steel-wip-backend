# tests/test_field.py
"""
현장 담당자 API 테스트 — GET /api/field/end (작업 완료 화면)

[픽스처 규칙]
  - MySQL server_default(now() 등)는 SQLite에서 동작하지 않으므로
    created_at, status 등 모든 컬럼 값을 픽스처에서 직접 지정한다.
  - 각 테스트는 conftest의 db_session/client를 공유하지 않으므로 독립적이다.
"""

from datetime import date, datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.models import Locations, SteelWip, Scenarios, Batch, BatchItems


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
# [dump 데이터 요약]
#   scenarios
#     id=1  scenario_order=0  status='ORDERED'  ← 현재 활성 시나리오
#     id=2  scenario_order=0  status=NULL       ← NOT NULL 위반으로 로드 skip
#     id=3  scenario_order=0  status='DRAFT'
#   batch
#     id=1,2,3 → scenario_id=1
#     id=4~9   → scenario_id=3
#   batch_items
#     모든 status = 'PENDING' 또는 'BEFORE_PENDING' (COMPLETED 없음)
#     batch 1 아이템: id=1~32 (PICKING 4개, RELOCATE/INBOUND 28개)
#   locations
#     id=1  'A-1',  id=2  'A-2',  id=3  'A-3',  id=4  'A-4'
#     id=5  'B-1',  id=6  'B-2',  id=7  'B-3'
#     id=8  'C-1',  id=9  'C-2'
#     id=15 'S4-1', id=16 'S4-2', id=17 'S4-3', id=18 'S4-4'
# ══════════════════════════════════════════════════════════════════════

async def test_integration_end_no_completed_batches(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 모든 batch_item이 PENDING/BEFORE_PENDING이므로
    완료된 배치가 없어 batch 목록은 비어 있어야 한다.
    진행률도 0.0이어야 한다.

    시나리오 1(ORDERED, scenario_order=0), 배치 1(batch_id=1) 사용.
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
    """
    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    assert response.status_code == 200
    data = response.json()["data"][0]
    assert data["scenarioTitle"] == "POSCO 건설 (30톤)-1"


async def test_integration_end_batch_from_other_scenario_rejected(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 다른 시나리오(id=3)에 속하는 배치(id=4)를
    전달하면 현재 시나리오(id=1)에 없으므로 빈 배열을 반환해야 한다.
    """
    # batch_id=4 는 scenario_id=3 소속 (시나리오 1과 다름)
    response = await client_with_dump.get("/api/field/end", params={"batchId": 4})

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_integration_end_completed_after_update(
    client_with_dump: AsyncClient, db_with_dump: AsyncSession
):
    """
    [통합] dump 실 데이터 기준 — 배치 1의 아이템을 모두 COMPLETED로 직접 업데이트 후
    호출하면 배치 1이 결과에 포함되고, 진행률이 0보다 커야 한다.

    배치 1 아이템: id=1~32 (총 32개)
    그 중 PICKING: id=1, 13, 22, 30  (from_location 각각 23, 6, 8, 1)
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

    # 진행률: 배치1(32개) COMPLETED / 전체(배치1+2+3) 아이템 수
    # 배치 2: 아이템 id=33~70 (38개), 배치 3: id=71~97 (27개)
    total_items = 32 + 38 + 27   # 97
    completed_items = 32
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
    배치 1, 아이템 1 (PICKING): from_location=23('ETC'), to_location=15('S4-1')
    """
    await db_with_dump.execute(
        text("UPDATE batch_items SET status='COMPLETED' WHERE batch_id=1")
    )
    await db_with_dump.commit()

    response = await client_with_dump.get("/api/field/end", params={"batchId": 1})

    picking_items = response.json()["data"][0]["batch"][0]["picking"]
    # batch_item id=1: from_location=23(ETC), to_location=15(S4-1), steel_wip_id=14
    item_1 = next(p for p in picking_items if p["batchItemId"] == 1)
    assert item_1["fromLocationName"] == "ETC"
    assert item_1["toLocationName"] == "S4-1"
