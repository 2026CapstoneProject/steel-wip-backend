# app/services/field_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import Scenarios, Batch, BatchItems, SteelWip, Locations
from app.schemas.field import (
    RelocationBatchItem,
    PickingBatchItem,
    FieldBatchGroup,
    FieldEndData,
)


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────

async def _build_batch_group(db: AsyncSession, batch: Batch) -> FieldBatchGroup:
    """
    Batch 하나를 받아 재배치 / 피킹 목록으로 분리한 FieldBatchGroup을 반환한다.
    batch_item_order 기준으로 정렬.
    """
    items_stmt = (
        select(BatchItems)
        .where(BatchItems.batch_id == batch.id)
        .order_by(BatchItems.batch_item_order)
    )
    items = (await db.execute(items_stmt)).scalars().all()

    relocation_items: list[RelocationBatchItem] = []
    picking_items: list[PickingBatchItem] = []

    for item in items:
        wip      = await db.get(SteelWip,  item.steel_wip_id) if item.steel_wip_id else None
        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        to_loc   = await db.get(Locations, item.to_location)   if item.to_location   else None

        if item.batch_item_action == "RELOCATE":
            relocation_items.append(RelocationBatchItem(
                batchItemId=item.id,
                wipId=wip.id if wip else 0,
                material=wip.material if wip else "",
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
            ))

        elif item.batch_item_action == "PICKING":
            picking_items.append(PickingBatchItem(
                batchItemId=item.id,
                wipId=wip.id if wip else 0,
                material=wip.material if wip else "",
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                # 원자재(wipId == 0)인 경우에만 규격 필드를 채운다
                thickness=wip.thickness if wip else None,
                width=wip.width if wip else None,
                height=wip.length if wip else None,  # DB length → height 매핑
            ))
        # INBOUND는 작업완료 화면에서는 표시 안 함 (명세서 기준)

    return FieldBatchGroup(relocation=relocation_items, picking=picking_items)


async def _is_batch_completed(db: AsyncSession, batch_id: int) -> bool:
    """
    해당 Batch의 모든 BatchItem이 COMPLETED 상태인지 확인한다.
    아이템이 하나도 없으면 False를 반환한다.
    """
    total_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id
    )
    total = (await db.execute(total_stmt)).scalar() or 0

    if total == 0:
        return False

    completed_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status == "COMPLETED",
    )
    completed = (await db.execute(completed_stmt)).scalar() or 0

    return total == completed


# ─────────────────────────────────────────────
# GET /api/field/end
# ─────────────────────────────────────────────

async def get_field_end(db: AsyncSession, batch_id: int) -> list:
    """
    작업 완료 화면
    1. 현재 진행 중인 시나리오 (scenario_order==0)를 먼저 확인한다.
    2. 전달받은 batch_id가 그 시나리오에 속하는지 검증한다.
    3. 시나리오 전체 진행률(완료 아이템 / 전체 아이템)을 계산한다.
    4. 해당 시나리오의 Batch 중 '완료된 Batch'(모든 아이템 COMPLETED)만 리턴한다.

    * 명세서의 GET + Request Body 구조는 HTTP 표준에 맞지 않아
      Query Parameter(?batchId=...)로 대체한다.

    * scenario_order==0 검증으로 현재 진행 중인 시나리오만 조회한다.
    """

    # 1. 현재 진행 중인 시나리오 (scenario_order == 0) 조회
    scenario_stmt = select(Scenarios).where(Scenarios.scenario_order == 0)
    scenario = (await db.execute(scenario_stmt)).scalars().first()

    if not scenario:
        return []

    # 2. 전달받은 batch_id가 현재 시나리오에 속하는지 검증
    target_batch_stmt = select(Batch).where(
        Batch.id == batch_id,
        Batch.scenario_id == scenario.id,
    )
    target_batch = (await db.execute(target_batch_stmt)).scalars().first()

    if not target_batch:
        return []  # batchId가 현재 시나리오에 속하지 않음

    # 2. 이 시나리오에 속한 모든 Batch ID 수집
    all_batch_ids_stmt = select(Batch.id).where(Batch.scenario_id == scenario.id)
    all_batch_ids: list[int] = (await db.execute(all_batch_ids_stmt)).scalars().all()

    # 3. 진행률 계산 (전체 아이템 수 / 완료된 아이템 수)
    total_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id.in_(all_batch_ids)
    )
    total: int = (await db.execute(total_stmt)).scalar() or 0

    completed_count_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id.in_(all_batch_ids),
        BatchItems.status == "COMPLETED",
    )
    completed_count: int = (await db.execute(completed_count_stmt)).scalar() or 0

    progress_rate = round(completed_count / total, 2) if total > 0 else 0.0

    # 4. 완료된 Batch만 필터링해서 그룹 빌드
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order)
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    completed_groups: list[FieldBatchGroup] = []
    for batch in all_batches:
        if not await _is_batch_completed(db, batch.id):
            continue
        group = await _build_batch_group(db, batch)
        completed_groups.append(group)

    return [
        FieldEndData(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            scenarioProgressRate=progress_rate,
            batch=completed_groups,
        )
    ]
