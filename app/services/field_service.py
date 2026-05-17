# app/services/field_service.py
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException

from app.models import (
    Scenarios,
    Batch,
    BatchItems,
    SteelWip,
    SteelWipStatus,
    Locations,
    QrCodes,
    LazerCutting,
    EstimatedWips,
)
from app.schemas.field import (
    RelocationBatchItem,
    PickingBatchItem,
    InboundBatchItem,
    FieldBatchGroup,
    FieldEndData,
    ProgressWipItem,
    ProgressLazerCutting,
    FieldProgressData,
    FieldReadyData,
    QrScanData,
    WipQrRequest,
    LocQrRequest,
    QrSaveRequest,
    QrSaveResult,
)
from app.schemas.field import FieldBatchItem, FieldWipDetail
from app.schemas.enums import BatchItemStatus

READY_ACTIONS = ["RELOCATE", "PICKING", "TEMP_MOVE", "RESTORE"]


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────

async def _resolve_batch_item_material_source(
    db: AsyncSession,
    item: BatchItems,
):
    action = (
        item.batch_item_action.value
        if hasattr(item.batch_item_action, "value")
        else str(item.batch_item_action)
    )
    if action == "INBOUND":
        if item.steel_wip_id:
            wip = await db.get(SteelWip, item.steel_wip_id)
            if wip is not None:
                qr = await db.get(QrCodes, wip.qr_id) if wip.qr_id else None
                return wip, None, qr

        if item.estimated_wip_id:
            estimated_wip = await db.get(EstimatedWips, item.estimated_wip_id)
            qr = (
                await db.get(QrCodes, estimated_wip.qr_id)
                if estimated_wip and estimated_wip.qr_id
                else None
            )
            return None, estimated_wip, qr

    wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
    qr = await db.get(QrCodes, wip.qr_id) if wip and wip.qr_id else None
    return wip, None, qr


async def _resolve_batch_item_lazer_cutting(
    db: AsyncSession,
    item: BatchItems,
) -> Optional[LazerCutting]:
    if item.estimated_wip_id:
        estimated_wip = await db.get(EstimatedWips, item.estimated_wip_id)
        if estimated_wip and estimated_wip.lazer_cutting_id:
            return await db.get(LazerCutting, estimated_wip.lazer_cutting_id)

    if item.steel_wip_id:
        stmt = (
            select(LazerCutting)
            .where(
                LazerCutting.batch_id == item.batch_id,
                LazerCutting.steel_wip_id == item.steel_wip_id,
            )
            .order_by(LazerCutting.id.asc())
        )
        return (await db.execute(stmt)).scalars().first()

    return None


def _build_virtual_input_source(
    lc: Optional[LazerCutting],
) -> tuple[Optional[str], str, Optional[float], Optional[float], Optional[float], Optional[float]]:
    if lc is None:
        return None, "", None, None, None, None
    return (
        "POSCO",
        lc.input_material or "",
        None,
        lc.input_width,
        lc.input_length,
        None,
    )


def _build_spec_text_from_values(
    thickness: Optional[float],
    width: Optional[float],
    length: Optional[float],
) -> Optional[str]:
    if thickness is None or width is None or length is None:
        return None
    return f"{_fmt_dim(thickness)}X{_fmt_dim(width)}X{_fmt_dim(length)}"


def _build_weight_text_from_value(weight: Optional[float]) -> Optional[str]:
    if weight is None:
        return None
    return f"{_fmt_dim(weight)}kg"


def _normalize_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _get_enum_value(value) -> Optional[str]:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _is_raw_material_wip(wip: Optional[SteelWip]) -> bool:
    return wip is not None and _get_enum_value(wip.status) == SteelWipStatus.RAW_MATERIAL.value


def _is_direct_start_item(
    action: str,
    wip: Optional[SteelWip],
    item: BatchItems,
    to_loc: Optional[Locations],
    estimated_wip: Optional[EstimatedWips] = None,
) -> bool:
    return bool(
        action == "RELOCATE"
        and estimated_wip is None
        and item.from_location is None
        and to_loc is not None
        and (to_loc.loc_name or "").startswith("S4-")
    )

async def _build_batch_group(
    db: AsyncSession,
    batch: Batch,
    exclude_completed: bool = False,
    only_completed: bool = False,
) -> FieldBatchGroup:
    items_stmt = (
        select(BatchItems)
        .where(BatchItems.batch_id == batch.id)
        .order_by(BatchItems.batch_item_order)
    )
    if exclude_completed:
        items_stmt = items_stmt.where(BatchItems.status != "COMPLETED")
    if only_completed:
        items_stmt = items_stmt.where(BatchItems.status == "COMPLETED")
    items = (await db.execute(items_stmt)).scalars().all()

    relocation_items: list[RelocationBatchItem] = []
    picking_items: list[PickingBatchItem] = []
    inbound_items: list[InboundBatchItem] = []

    for item in items:
        wip, estimated_wip, qr = await _resolve_batch_item_material_source(db, item)
        lc = await _resolve_batch_item_lazer_cutting(db, item)
        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        action = (
            item.batch_item_action.value
            if hasattr(item.batch_item_action, "value")
            else str(item.batch_item_action)
        )

        wip_qr = qr.qr_code if qr else None
        if estimated_wip:
            manufacturer = estimated_wip.manufacturer
            material = estimated_wip.material
            thickness = estimated_wip.thickness
            width = estimated_wip.width
            height = estimated_wip.length
            weight = estimated_wip.weight
        elif wip:
            manufacturer = wip.manufacturer
            material = wip.material
            thickness = wip.thickness
            width = wip.width
            height = wip.length
            weight = wip.weight
        else:
            manufacturer, material, thickness, width, height, weight = _build_virtual_input_source(lc)
        spec_text = _build_spec_text_from_values(thickness, width, height)
        weight_text = _build_weight_text_from_value(weight)
        resolved_wip_id = item.steel_wip_id or (wip.id if wip else 0)

        is_direct_start = _is_direct_start_item(
            action=action,
            wip=wip,
            item=item,
            to_loc=to_loc,
            estimated_wip=estimated_wip,
        )

        if is_direct_start:
            picking_items.append(PickingBatchItem(
                batchItemId=item.id,
                actionType="DIRECT_START",
                wipId=resolved_wip_id,
                wipQr=wip_qr,
                manufacturer=manufacturer,
                specText=spec_text,
                weightText=weight_text,
                material=material,
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
                expectedStartTime=item.expected_start_time or 0,
                thickness=thickness,
                width=width,
                height=height,
            ))
        elif action in ("RELOCATE", "TEMP_MOVE", "RESTORE"):
            relocation_items.append(RelocationBatchItem(
                batchItemId=item.id,
                actionType=action,
                wipId=resolved_wip_id,
                wipQr=wip_qr,
                manufacturer=manufacturer,
                specText=spec_text,
                weightText=weight_text,
                material=material,
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
                expectedStartTime=item.expected_start_time or 0,
            ))

        elif action == "PICKING":
            picking_items.append(PickingBatchItem(
                batchItemId=item.id,
                actionType=action,
                wipId=resolved_wip_id,
                wipQr=wip_qr,
                manufacturer=manufacturer,
                specText=spec_text,
                weightText=weight_text,
                material=material,
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
                expectedStartTime=item.expected_start_time or 0,
                thickness=thickness,
                width=width,
                height=height,
            ))

        elif action == "INBOUND":
            inbound_items.append(InboundBatchItem(
                batchItemId=item.id,
                actionType=action,
                wipId=resolved_wip_id,
                wipQr=wip_qr,
                manufacturer=manufacturer,
                specText=spec_text,
                weightText=weight_text,
                material=material,
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
                expectedStartTime=item.expected_start_time or 0,
                thickness=thickness,
                width=width,
                height=height,
            ))

    return FieldBatchGroup(
        relocation=relocation_items,
        picking=picking_items,
        inbound=inbound_items,
    )
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

# ✅ 신규 추가 — Batch 완료 처리 (completed_at 기록)
async def _complete_batch(db: AsyncSession, batch_id: int) -> None:
    """
    Batch의 모든 BatchItem이 COMPLETED 상태가 됐을 때 호출.
    Batch.completed_at을 현재 시각으로 기록한다.
    이미 completed_at이 있는 경우 멱등성 보장(재호출 시 무시).
    """
    batch = await db.get(Batch, batch_id)
    if not batch or batch.completed_at is not None:
        return
    batch.completed_at = datetime.now(timezone.utc)
    await db.commit()

async def _has_incomplete_no_wip_batch(db: AsyncSession, scenario_id: int) -> bool:
    """
    해당 시나리오에서 아직 completed_at이 없고,
    INBOUND 아이템이 하나도 없는 배치가 존재하는지 확인.
    → 재공품 없는 배치가 '생산완료' 버튼을 아직 누르지 않은 상태인지 판단.
    """
    all_batches_stmt = (
        select(Batch)
        .where(
            Batch.scenario_id == scenario_id,
            Batch.completed_at.is_(None),  # 아직 완료 안 된 배치
        )
    )
    incomplete_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in incomplete_batches:
        # PICKING/RELOCATE가 모두 완료됐는지 확인 (생산 중 단계에 진입한 배치만)
        pending_ready = await _count_incomplete_items_in_batch_actions(
            db, batch.id, READY_ACTIONS
        )
        if pending_ready > 0:
            continue  # 아직 생산 준비 단계 → 해당 없음

        # INBOUND 아이템 개수 확인
        total_inbound_stmt = select(func.count(BatchItems.id)).where(
            BatchItems.batch_id == batch.id,
            BatchItems.batch_item_action == "INBOUND",
        )
        total_inbound = (await db.execute(total_inbound_stmt)).scalar() or 0

        if total_inbound == 0:
            return True  # 재공품 없는 미완료 배치 발견

    return False

async def _get_current_active_scenario(db: AsyncSession) -> Optional[Scenarios]:
    """
    현재 현장 기준이 되는 시나리오를 반환한다.
    Field에서는 발행된 시나리오(ORDERED/IN_PROGRESS)만 활성 시나리오로 본다.
    아직 발행되지 않은 DRAFT/None 시나리오는 Office 전용 상태이므로 제외한다.
    """
    scenario_stmt = (
        select(Scenarios)
        .where(Scenarios.status.in_(["ORDERED", "IN_PROGRESS"]))
        .order_by(Scenarios.scenario_order.asc())
    )
    return (await db.execute(scenario_stmt)).scalars().first()


async def _get_first_incomplete_batch(
    db: AsyncSession, scenario_id: int
) -> Optional[Batch]:
    """
    주어진 시나리오에서 아직 완료되지 않은 첫 번째 배치를 반환한다.
    """
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario_id)
        .order_by(Batch.batch_order.asc())
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in all_batches:
        if not await _is_batch_completed(db, batch.id):
            return batch

    return None


async def _get_first_batch_with_pending_inbound(
    db: AsyncSession,
    scenario_id: int,
) -> Optional[Batch]:
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario_id)
        .order_by(Batch.batch_order.asc())
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in all_batches:
        pending_inbound_count = await _count_incomplete_items_in_batch(
            db,
            batch.id,
            action="INBOUND",
        )
        if pending_inbound_count > 0:
            return batch

    return None


async def _count_incomplete_items_in_batch(
    db: AsyncSession,
    batch_id: int,
    action: Optional[str] = None,
) -> int:
    stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status != "COMPLETED",
    )
    if action:
        stmt = stmt.where(BatchItems.batch_item_action == action)
    return (await db.execute(stmt)).scalar() or 0


async def _count_total_items_in_batch(
    db: AsyncSession,
    batch_id: int,
    action: Optional[str] = None,
) -> int:
    stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id,
    )
    if action:
        stmt = stmt.where(BatchItems.batch_item_action == action)
    return (await db.execute(stmt)).scalar() or 0


async def _count_incomplete_items_in_batch_actions(
    db: AsyncSession,
    batch_id: int,
    actions: list[str],
) -> int:
    stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status != "COMPLETED",
        BatchItems.batch_item_action.in_(actions),
    )
    return (await db.execute(stmt)).scalar() or 0


async def _count_incomplete_picking_start_items(db: AsyncSession, batch_id: int) -> int:
    items_stmt = select(BatchItems).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status != "COMPLETED",
    )
    items = (await db.execute(items_stmt)).scalars().all()

    count = 0
    for item in items:
        action = (
            item.batch_item_action.value
            if hasattr(item.batch_item_action, "value")
            else str(item.batch_item_action)
        )
        if action == "PICKING":
            count += 1
            continue
        if action != "RELOCATE":
            continue

        wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        is_direct_start = _is_direct_start_item(
            action=action,
            wip=wip,
            item=item,
            to_loc=to_loc,
        )
        if is_direct_start:
            count += 1

    return count


async def _has_started_processing_item(db: AsyncSession, batch_id: int) -> bool:
    items_stmt = select(BatchItems).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status == "COMPLETED",
    )
    items = (await db.execute(items_stmt)).scalars().all()

    for item in items:
        action = (
            item.batch_item_action.value
            if hasattr(item.batch_item_action, "value")
            else str(item.batch_item_action)
        )
        if action == "PICKING":
            return True
        if action != "RELOCATE":
            continue

        wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        is_direct_start = _is_direct_start_item(
            action=action,
            wip=wip,
            item=item,
            to_loc=to_loc,
        )
        if is_direct_start:
            return True

    return False


async def _get_active_ready_batch(db: AsyncSession, scenario_id: int) -> Optional[Batch]:
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario_id)
        .order_by(Batch.batch_order.asc())
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in all_batches:
        pending_ready_count = await _count_incomplete_items_in_batch_actions(
            db,
            batch.id,
            READY_ACTIONS,
        )
        if pending_ready_count > 0:
            return batch

        pending_total = await _count_incomplete_items_in_batch(db, batch.id)
        if pending_total > 0:
            # 현재 배치가 생산 중(INBOUND만 남음)이면 뒤 배치는 아직 ready에 노출하지 않는다.
            return None

    return None

async def _get_active_processing_batch(db: AsyncSession, scenario_id: int) -> Optional[Batch]:
    active_state = await _get_active_processing_state(db, scenario_id)
    return active_state["batch"] if active_state else None


async def _get_processing_start_item(
    db: AsyncSession,
    batch_id: int,
    lc: LazerCutting,
) -> Optional[BatchItems]:
    if lc.steel_wip_id:
        raw_stmt = (
            select(BatchItems)
            .where(
                BatchItems.batch_id == batch_id,
                BatchItems.steel_wip_id == lc.steel_wip_id,
                BatchItems.batch_item_action == "RELOCATE",
            )
            .order_by(BatchItems.batch_item_order.asc(), BatchItems.id.asc())
        )
        raw_items = (await db.execute(raw_stmt)).scalars().all()
        for item in raw_items:
            wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
            to_loc = await db.get(Locations, item.to_location) if item.to_location else None
            if _is_direct_start_item(
                action="RELOCATE",
                wip=wip,
                item=item,
                to_loc=to_loc,
            ):
                return item

        pick_stmt = (
            select(BatchItems)
            .where(
                BatchItems.batch_id == batch_id,
                BatchItems.steel_wip_id == lc.steel_wip_id,
                BatchItems.batch_item_action == "PICKING",
            )
            .order_by(BatchItems.batch_item_order.asc(), BatchItems.id.asc())
        )
        return (await db.execute(pick_stmt)).scalars().first()

    raw_stmt = (
        select(BatchItems)
        .where(
            BatchItems.batch_id == batch_id,
            BatchItems.batch_item_action == "RELOCATE",
        )
        .order_by(BatchItems.batch_item_order.asc(), BatchItems.id.asc())
    )
    raw_items = (await db.execute(raw_stmt)).scalars().all()
    for item in raw_items:
        wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        if _is_direct_start_item(
            action="RELOCATE",
            wip=wip,
            item=item,
            to_loc=to_loc,
        ):
            return item
    return None


async def _compute_batch_consumed_minutes(
    db: AsyncSession,
    batch_id: int,
    lazer_cuttings: list[LazerCutting],
) -> int:
    now = datetime.now(timezone.utc)
    consumed_minutes = 0

    for lc in lazer_cuttings:
        start_item = await _get_processing_start_item(db, batch_id, lc)
        start_timestamp = None
        if start_item and start_item.status == "COMPLETED":
            start_timestamp = _normalize_utc_datetime(
                start_item.destination_scanned_at or start_item.item_scanned_at
            )

        estimated_cutting_time = lc.estimated_cutting_time or 0
        if start_timestamp and estimated_cutting_time > 0:
            elapsed_minutes = max(
                0,
                int((now - start_timestamp).total_seconds() // 60),
            )
            consumed_minutes += min(elapsed_minutes, estimated_cutting_time)

    return consumed_minutes

# 2026-04-30 추가 헬퍼
def _fmt_text_number(v: float | None) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{v}".rstrip("0").rstrip(".")


def _build_spec_text(wip: SteelWip | None) -> str | None:
    if not wip:
        return None
    return (
        f"{_fmt_text_number(wip.thickness)}X"
        f"{_fmt_text_number(wip.width)}X"
        f"{_fmt_text_number(wip.length)}"
    )


def _build_weight_text(wip: SteelWip | None) -> str | None:
    if not wip or wip.weight is None:
        return None
    return f"{_fmt_text_number(wip.weight)}kg"

async def _get_next_stack_level(db: AsyncSession, location_id: int) -> int:
    """
    해당 location에 있는 SteelWip 중 stack_level 최댓값을 구하고,
    그보다 1 높은 값을 반환한다. (비어있으면 1 반환)
    """
    stmt = select(func.max(SteelWip.stack_level)).where(
        SteelWip.location_id == location_id
    )
    max_level = (await db.execute(stmt)).scalar()
    return (max_level or 0) + 1

# ─────────────────────────────────────────────
# GET /api/field/end
# ─────────────────────────────────────────────

async def get_field_end(db: AsyncSession, batch_id: Optional[int] = None) -> list:
    """
    작업 완료 화면
    1. 현재 진행 중인 시나리오 (최소 scenario_order)를 먼저 확인한다.
    2. batch_id가 제공된 경우 해당 배치가 시나리오에 속하는지 검증한다 (생략 가능).
    3. 시나리오 전체 진행률(완료 아이템 / 전체 아이템)을 계산한다.
    4. 각 Batch에서 COMPLETED 상태 아이템만 추출하여 반환한다.
    """

    # 1. 현재 진행 중인 시나리오 (최소 scenario_order) 조회
    scenario = await _get_current_active_scenario(db)

    if not scenario:
        return []

    # 2. batch_id 제공 시 해당 배치가 현재 시나리오에 속하는지 검증
    if batch_id is not None:
        target_batch_stmt = select(Batch).where(
            Batch.id == batch_id,
            Batch.scenario_id == scenario.id,
        )
        target_batch = (await db.execute(target_batch_stmt)).scalars().first()
        if not target_batch:
            return []  # batchId가 현재 시나리오에 속하지 않음

    # 3. 이 시나리오에 속한 모든 Batch ID 수집
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

    # ✅ 재공품 없는 미완료 배치가 있으면 분모에 1 추가
    has_incomplete_no_wip_batch = await _has_incomplete_no_wip_batch(db, scenario.id)
    effective_total = total + (1 if has_incomplete_no_wip_batch else 0)

    progress_rate = round(completed_count / effective_total, 2) if effective_total > 0 else 0.0
    remaining_count = max(effective_total - completed_count, 0)

    # 4. ✅ 현장에서 완료된 모든 작업 조회 (배치 전체 완료 여부와 관계없음)
    # "작업 완료" = 현장에서 실제로 완료된 아이템들 (상태=COMPLETED)
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order)
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    completed_groups: list[FieldBatchGroup] = []
    for batch in all_batches:
        # 배치 내 완료된 아이템만 필터링 (배치 전체 완료 여부는 상관없음)
        group = await _build_batch_group(db, batch, only_completed=True)

        # 완료된 아이템이 하나라도 있으면 배치 그룹 추가
        has_completed_items = bool(group.relocation or group.picking or group.inbound)
        if has_completed_items:
            completed_groups.append(group)

    return [
        FieldEndData(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            scenarioProgressRate=progress_rate,
            completedTaskCount=completed_count,
            totalTaskCount=total,
            remainingTaskCount=remaining_count,
            batch=completed_groups,
        )
    ]


async def get_live_field_data(db: AsyncSession, lazer_name: str) -> List[FieldBatchItem]:
    scenario_stmt = (
        select(Scenarios)
        .where(
            Scenarios.lazer_name == lazer_name,
            Scenarios.status.in_(["ORDERED", "IN_PROGRESS"]),
        )
        .order_by(Scenarios.scenario_order.asc())
    )
    scenario = (await db.execute(scenario_stmt)).scalars().first()
    if not scenario:
        return []

    batch_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order.asc())
    )
    batches = (await db.execute(batch_stmt)).scalars().all()
    if not batches:
        return []
    batch_map = {batch.id: batch for batch in batches}
    batch_ids = list(batch_map.keys())
    item_stmt = (
        select(BatchItems)
        .where(
            BatchItems.batch_id.in_(batch_ids),
            # COMPLETED도 포함 — 카드는 유지하고 배지만 바꿈
            # (시나리오 자체가 COMPLETED될 때 위의 scenario 쿼리에서 이미 필터됨)
        )
        .order_by(
            BatchItems.expected_start_time.asc(),  # ✅ 예상 시작 시간 우선 정렬
            BatchItems.batch_id.asc(),
            BatchItems.batch_item_order.asc()
        )
    )
    batch_items = (await db.execute(item_stmt)).scalars().all()

    response_list = []
    
    for item in batch_items:
        wip_detail_list = []

        # 4. WIP 데이터 및 QR 코드 조회
        wip = None
        estimated_wip = None

        # 4-1. SteelWip 조회 (일반적인 경우)
        if item.steel_wip_id:
            wip = await db.get(SteelWip, item.steel_wip_id)

        # 4-2. EstimatedWips 조회 (INBOUND인 경우) ✅
        if item.batch_item_action == "INBOUND" and item.estimated_wip_id:
            estimated_wip = await db.get(EstimatedWips, item.estimated_wip_id)

        # 4-3. WIP 또는 EstimatedWip 정보 처리
        lc = await _resolve_batch_item_lazer_cutting(db, item)
        if wip or estimated_wip or lc:
            source_wip = estimated_wip if estimated_wip else wip
            qr_code_val = None
            if source_wip and source_wip.qr_id:
                qr = await db.get(QrCodes, source_wip.qr_id)
                if qr:
                    qr_code_val = qr.qr_code
            nc_code_val = lc.nc_code if lc else None
            if source_wip:
                manufacturer = source_wip.manufacturer or ""
                material = source_wip.material or ""
                thickness = source_wip.thickness
                width = source_wip.width
                length = source_wip.length
                weight = source_wip.weight
            else:
                manufacturer, material, thickness, width, length, weight = _build_virtual_input_source(lc)

            # float 값들을 명세서 형식인 str로 변환
            wip_detail_list.append(FieldWipDetail(
                qrId=qr_code_val,
                material=material,
                manufacturer=manufacturer,
                thickness=str(thickness) if thickness else "0",
                width=str(width) if width else "0",
                length=str(length) if length else "0",
                weight=str(weight) if weight else "0",
                ncCode=nc_code_val,
            ))

        # 5. 출발지, 도착지 구역 이름 조회
        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        batch = batch_map.get(item.batch_id)

        # 6. 스키마에 맞게 조립
        response_list.append(FieldBatchItem(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            lazerName=lazer_name,
            batchId=item.batch_id,
            batchOrder=batch.batch_order if batch else None,
            batchItemId=str(item.id),
            status=item.status,
            batchItemAction=item.batch_item_action,
            wip=wip_detail_list,
            expectedStartTime=str(item.expected_start_time or 0),
            expectedRunningTime=str(item.expected_running_time or 0),
            fromLocationName=from_loc.loc_name if from_loc else None,
            toLocationName=to_loc.loc_name if to_loc else None
        ))

    return response_list


async def _get_ordered_field_items_for_scenario(
    db: AsyncSession,
    scenario: Scenarios,
    exclude_completed: bool = True,
) -> List[FieldBatchItem]:
    batch_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order.asc())
    )
    batches = (await db.execute(batch_stmt)).scalars().all()
    if not batches:
        return []

    batch_map = {batch.id: batch for batch in batches}
    batch_ids = list(batch_map.keys())

    item_stmt = (
        select(BatchItems)
        .where(BatchItems.batch_id.in_(batch_ids))
        .order_by(
            BatchItems.expected_start_time.asc(),
            BatchItems.batch_id.asc(),
            BatchItems.batch_item_order.asc(),
        )
    )
    if exclude_completed:
        item_stmt = item_stmt.where(BatchItems.status != "COMPLETED")

    batch_items = (await db.execute(item_stmt)).scalars().all()
    response_list: list[FieldBatchItem] = []

    for item in batch_items:
        wip_detail_list = []
        wip = None
        estimated_wip = None

        if item.steel_wip_id:
            wip = await db.get(SteelWip, item.steel_wip_id)

        if item.batch_item_action == "INBOUND" and item.estimated_wip_id:
            estimated_wip = await db.get(EstimatedWips, item.estimated_wip_id)

        lc = await _resolve_batch_item_lazer_cutting(db, item)
        if wip or estimated_wip or lc:
            source_wip = estimated_wip if estimated_wip else wip
            qr_code_val = None
            if source_wip and source_wip.qr_id:
                qr = await db.get(QrCodes, source_wip.qr_id)
                if qr:
                    qr_code_val = qr.qr_code
            nc_code_val = lc.nc_code if lc else None
            if source_wip:
                manufacturer = source_wip.manufacturer or ""
                material = source_wip.material or ""
                thickness = source_wip.thickness
                width = source_wip.width
                length = source_wip.length
                weight = source_wip.weight
            else:
                manufacturer, material, thickness, width, length, weight = _build_virtual_input_source(lc)

            wip_detail_list.append(FieldWipDetail(
                qrId=qr_code_val,
                material=material,
                manufacturer=manufacturer,
                thickness=str(thickness) if thickness else "0",
                width=str(width) if width else "0",
                length=str(length) if length else "0",
                weight=str(weight) if weight else "0",
                ncCode=nc_code_val,
            ))

        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        batch = batch_map.get(item.batch_id)

        response_list.append(FieldBatchItem(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            lazerName=scenario.lazer_name,
            batchId=item.batch_id,
            batchOrder=batch.batch_order if batch else None,
            batchItemId=str(item.id),
            status=item.status,
            batchItemAction=item.batch_item_action,
            wip=wip_detail_list,
            expectedStartTime=str(item.expected_start_time or 0),
            expectedRunningTime=str(item.expected_running_time or 0),
            fromLocationName=from_loc.loc_name if from_loc else None,
            toLocationName=to_loc.loc_name if to_loc else None,
        ))

    return response_list


# ─────────────────────────────────────────────
# GET /api/field/progress  —  생산 중 화면
# ─────────────────────────────────────────────

def _fmt_dim(v: float | None) -> str:
    """치수 숫자를 wipName 문자열용으로 변환. 정수면 소수점 제거."""
    if v is None:
        return "0"
    return str(int(v)) if v == int(v) else str(v)


async def _build_progress_wip_item(
    db: AsyncSession,
    inbound_item: BatchItems,
    estimated_wip: EstimatedWips,
    cutting_done: bool = False,  # ✅ 절단 완료 여부 파라미터 추가
) -> ProgressWipItem:
    qr_code_value = None
    if estimated_wip.qr_id:
        qr = await db.get(QrCodes, estimated_wip.qr_id)
        qr_code_value = qr.qr_code if qr else None

    to_loc = await db.get(Locations, inbound_item.to_location) if inbound_item.to_location else None

    wip_name = (
        f"{_fmt_dim(estimated_wip.thickness)}"
        f"X{_fmt_dim(estimated_wip.width)}"
        f"X{_fmt_dim(estimated_wip.length)}"
    )

    spec_text = (
        f"{_fmt_dim(estimated_wip.thickness)} x "
        f"{_fmt_dim(estimated_wip.width)} x "
        f"{_fmt_dim(estimated_wip.length)}"
    )

    weight_text = (
        f"{_fmt_dim(estimated_wip.weight)} kg"
        if estimated_wip.weight is not None
        else None
    )

    # ✅ cutting_done 기반으로 상태 결정
    # - 절단 완료: "적재 대기" (스캔하여 적재 가능)
    # - 절단 진행중: "생성 대기" (아직 적재 불가, 표시만)
    item_status = "적재 대기" if cutting_done else "생성 대기"

    return ProgressWipItem(
        wipId=estimated_wip.id,
        batchItemId=inbound_item.id,
        wipQr=qr_code_value,
        manufacturer=estimated_wip.manufacturer,
        material=estimated_wip.material,
        specText=spec_text,
        weightText=weight_text,
        wipStatus="GENERATED",
        wipName=wip_name,
        toLocation=to_loc.loc_name if to_loc else None,
        status=item_status,
    )


async def _get_pending_inbound_entries(
    db: AsyncSession,
    batch_id: int,
) -> list[dict]:
    pending_inbound_items_stmt = (
        select(BatchItems)
        .where(
            BatchItems.batch_id == batch_id,
            BatchItems.batch_item_action == "INBOUND",
            BatchItems.status != "COMPLETED",
            BatchItems.estimated_wip_id.is_not(None),
        )
        .order_by(BatchItems.batch_item_order.asc(), BatchItems.id.asc())
    )
    pending_inbound_items = (await db.execute(pending_inbound_items_stmt)).scalars().all()

    entries = []
    for inbound_item in pending_inbound_items:
        estimated_wip = await db.get(EstimatedWips, inbound_item.estimated_wip_id)
        if estimated_wip is None:
            continue
        entries.append({
            "item": inbound_item,
            "estimated_wip": estimated_wip,
            "lazer_cutting_id": estimated_wip.lazer_cutting_id,
        })
    return entries


async def _auto_complete_preceding_no_wip_batches(
    db: AsyncSession,
    batch_id: int,
) -> None:
    """
    PICKING / DIRECT_START 완료 시 호출.
    현재 배치보다 순서가 앞선 배치 중 아래 조건을 모두 만족하면 자동 완료 처리한다.
      - INBOUND 아이템 없음 (재공품 없는 배치)
      - 미완료 BatchItem 없음 (모든 작업 COMPLETED)
      - completed_at 없음 (아직 완료 처리 안 됨)
    "A 생산이 재공품 X & 다음 생산 피킹 완료 → A 생산은 완료로 간주"
    """
    batch = await db.get(Batch, batch_id)
    if not batch:
        return

    preceding_stmt = (
        select(Batch)
        .where(
            Batch.scenario_id == batch.scenario_id,
            Batch.batch_order < batch.batch_order,
            Batch.completed_at.is_(None),
        )
        .order_by(Batch.batch_order.asc())
    )
    preceding_batches = (await db.execute(preceding_stmt)).scalars().all()

    now = datetime.now(timezone.utc)
    changed = False
    for prev_batch in preceding_batches:
        # WIP 있는 배치는 자동 완료 안 함
        total_inbound = await _count_total_items_in_batch(db, prev_batch.id, action="INBOUND")
        if total_inbound > 0:
            continue
        # 미완료 작업이 남아있으면 자동 완료 안 함
        pending = await _count_incomplete_items_in_batch(db, prev_batch.id)
        if pending > 0:
            continue
        prev_batch.completed_at = now
        changed = True

    if changed:
        await db.commit()


async def _get_current_processing_context(
    db: AsyncSession,
    batch: Batch,
    lazer_cuttings: list[LazerCutting],
    pending_inbound_entries: Optional[list[dict]] = None,
) -> Optional[dict]:
    if pending_inbound_entries is None:
        pending_inbound_entries = await _get_pending_inbound_entries(db, batch.id)

    now = datetime.now(timezone.utc)
    started_states: list[dict] = []

    for lc in lazer_cuttings:
        lc_status = lc.status.value if hasattr(lc.status, "value") else str(lc.status)
        if lc_status == "COMPLETED":
            continue

        start_item = await _get_processing_start_item(db, batch.id, lc)
        if start_item is None or start_item.status != "COMPLETED":
            continue

        start_timestamp = _normalize_utc_datetime(
            start_item.destination_scanned_at or start_item.item_scanned_at
        )
        estimated_cutting_time = lc.estimated_cutting_time or 0
        cutting_done = bool(
            start_timestamp is not None
            and (
                estimated_cutting_time <= 0
                or int((now - start_timestamp).total_seconds() // 60) >= estimated_cutting_time
            )
        )

        linked_pending_entries = [
            entry for entry in pending_inbound_entries
            if entry["lazer_cutting_id"] == lc.id
        ]
        output_count = (
            await db.execute(
                select(func.count(EstimatedWips.id)).where(
                    EstimatedWips.lazer_cutting_id == lc.id
                )
            )
        ).scalar() or 0

        # 실제 미완료 INBOUND가 연결되어 있으면 이미 현장에서 적재해야 할
        # 발생 재공품이 존재한다는 뜻이므로, 시간 추정보다 이 상태를 우선한다.
        if linked_pending_entries:
            started_states.append({
                "lc": lc,
                "start_item": start_item,
                "start_timestamp": start_timestamp,
                "start_order": start_item.batch_item_order or 0,
                "linked_pending_entries": linked_pending_entries,
                "cutting_done": True,
                "has_output": True,
            })
            continue

        started_states.append({
            "lc": lc,
            "start_item": start_item,
            "start_timestamp": start_timestamp,
            "start_order": start_item.batch_item_order or 0,
            "linked_pending_entries": linked_pending_entries,
            "cutting_done": cutting_done,
            "has_output": output_count > 0,
        })

    started_states.sort(
        key=lambda state: (
            state["start_order"],
            state["start_timestamp"] or datetime.min.replace(tzinfo=timezone.utc),
            state["lc"].id,
        ),
        reverse=True,
    )

    for state in started_states:
        if not state["cutting_done"]:
            # 절단이 아직 진행 중인 현재 Job.
            # 이 단계에서는 아직 미래 Job의 발생 재공품을 보여주면 안 된다.
            if state["has_output"] and not state["linked_pending_entries"]:
                continue
            return {
                "lc": state["lc"],
                "linked_pending_entries": state["linked_pending_entries"],
                "cutting_done": False,
                "has_output": state["has_output"],
            }

        if state["has_output"] and state["linked_pending_entries"]:
            return {
                "lc": state["lc"],
                "linked_pending_entries": state["linked_pending_entries"],
                "cutting_done": True,
                "has_output": True,
            }

        if not state["has_output"]:
            return {
                "lc": state["lc"],
                "linked_pending_entries": [],
                "cutting_done": True,
                "has_output": False,
            }

    return None


async def _get_active_processing_state(
    db: AsyncSession,
    scenario_id: int,
) -> Optional[dict]:
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario_id)
        .order_by(Batch.batch_order.asc())
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in all_batches:
        if batch.completed_at is not None:
            continue

        if not await _has_started_processing_item(db, batch.id):
            continue

        lc_stmt = (
            select(LazerCutting)
            .where(LazerCutting.batch_id == batch.id)
            .order_by(LazerCutting.id.asc())
        )
        lazer_cuttings = (await db.execute(lc_stmt)).scalars().all()
        pending_inbound_entries = await _get_pending_inbound_entries(db, batch.id)
        context = await _get_current_processing_context(
            db,
            batch,
            lazer_cuttings,
            pending_inbound_entries,
        )
        if context is None:
            continue

        return {
            "batch": batch,
            "lazer_cuttings": lazer_cuttings,
            "pending_inbound_entries": pending_inbound_entries,
            "context": context,
        }

    return None


async def _build_field_progress_for_scenario(
    db: AsyncSession,
    scenario: Scenarios,
) -> Optional[FieldProgressData]:
    active_state = await _get_active_processing_state(db, scenario.id)
    if not active_state:
        return None

    batch: Batch = active_state["batch"]
    lazer_cuttings: list[LazerCutting] = active_state["lazer_cuttings"]
    pending_inbound_entries: list[dict] = active_state["pending_inbound_entries"]
    context: dict = active_state["context"]
    current_lc: LazerCutting = context["lc"]

    expected_total = sum(lc.estimated_cutting_time or 0 for lc in lazer_cuttings)
    pending_start_count = await _count_incomplete_picking_start_items(db, batch.id)
    pending_inbound_count = len(pending_inbound_entries)
    total_inbound_count_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch.id,
        BatchItems.batch_item_action == "INBOUND",
    )
    total_inbound_count = (await db.execute(total_inbound_count_stmt)).scalar() or 0

    has_no_wip = not context["has_output"]
    consumed_minutes = await _compute_batch_consumed_minutes(db, batch.id, lazer_cuttings)
    input_wip = await db.get(SteelWip, current_lc.steel_wip_id) if current_lc.steel_wip_id else None
    input_wip_id = input_wip.id if input_wip else 0
    material = input_wip.material if input_wip else (current_lc.input_material or "")
    estimated_cutting_time = current_lc.estimated_cutting_time or 0

    current_wip_items: list[ProgressWipItem] = []
    if context["cutting_done"] and context["has_output"]:
        for entry in context["linked_pending_entries"]:
            current_wip_items.append(
                await _build_progress_wip_item(
                    db,
                    entry["item"],
                    entry["estimated_wip"],
                    cutting_done=True,
                )
            )

    lc_groups = [
        ProgressLazerCutting(
            lazerCuttingId=current_lc.id,
            inputWipId=input_wip_id,
            material=material,
            estimatedCuttingTime=estimated_cutting_time,
            wip=current_wip_items,
        )
    ]

    batch_progress_rate = (
        round(min(consumed_minutes / expected_total, 1.0), 2)
        if expected_total > 0
        else 0.0
    )
    can_complete_production = bool(not context["has_output"])

    active_remaining = len(current_wip_items)

    if has_no_wip:
        batch_remaining = 1 if batch.completed_at is None else 0
        batch_completed = 1 if batch.completed_at is not None else 0
        batch_total = 1
    else:
        batch_remaining = active_remaining
        batch_completed = max(total_inbound_count - pending_inbound_count, 0)
        batch_total = total_inbound_count

    return FieldProgressData(
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        batchProgressRate=batch_progress_rate,
        completedTaskCount=batch_completed,
        totalTaskCount=batch_total,
        remainingTaskCount=batch_remaining,
        expectedTotalRunningTime=expected_total,
        lazer_cutting=lc_groups,
        hasNoWip=has_no_wip,
        batchId=batch.id,
        canCompleteProduction=can_complete_production,
    )


async def get_field_progress(db: AsyncSession) -> list:
    """
    생산 중 화면
    - 발행된 시나리오(ORDERED/IN_PROGRESS)를 순서대로 조회한다.
    - 각 시나리오에서 현재 활성 생산 컨텍스트 1개만 조립한다.
    """
    scenario_stmt = (
        select(Scenarios)
        .where(
            Scenarios.status.in_(["ORDERED", "IN_PROGRESS"]),
            Scenarios.scenario_order > 0,
        )
        .order_by(Scenarios.scenario_order.asc())
    )
    scenarios = (await db.execute(scenario_stmt)).scalars().all()
    if not scenarios:
        return []

    results: list[FieldProgressData] = []
    for scenario in scenarios:
        progress_data = await _build_field_progress_for_scenario(db, scenario)
        if progress_data is not None:
            results.append(progress_data)

    return results


# ─────────────────────────────────────────────
# GET /api/field/ready  —  생산 준비 화면
# ─────────────────────────────────────────────

async def get_field_ready(db: AsyncSession) -> list:
    """
    생산 준비 화면
    - 발행된 시나리오(ORDERED/IN_PROGRESS, scenario_order > 0)를 scenario_order ASC로 전체 조회한다.
    - 각 시나리오별로 진행률, 배치 그룹, 다음 시나리오 정보를 함께 반환한다.
    """

    # ── 1. 발행된 시나리오 전체를 순서대로 조회 ──────────────────────────
    scenario_stmt = (
        select(Scenarios)
        .where(
            Scenarios.status.in_(["ORDERED", "IN_PROGRESS"]),
            Scenarios.scenario_order > 0,
        )
        .order_by(Scenarios.scenario_order.asc())
    )
    scenarios = (await db.execute(scenario_stmt)).scalars().all()

    if not scenarios:
        return []

    result = []

    for i, scenario in enumerate(scenarios):
        # 다음 시나리오 (순서상 바로 다음)
        next_scenario = scenarios[i + 1] if i + 1 < len(scenarios) else None

        # ── 2. 현재 시나리오의 모든 Batch ID 수집 ───────────────────────
        all_batch_ids_stmt = select(Batch.id).where(Batch.scenario_id == scenario.id)
        all_batch_ids: list[int] = (await db.execute(all_batch_ids_stmt)).scalars().all()

        # ── 3. 진행률 계산 ────────────────────────────────────────────────
        total_stmt = select(func.count(BatchItems.id)).where(
            BatchItems.batch_id.in_(all_batch_ids)
        )
        total: int = (await db.execute(total_stmt)).scalar() or 0

        completed_count_stmt = select(func.count(BatchItems.id)).where(
            BatchItems.batch_id.in_(all_batch_ids),
            BatchItems.status == "COMPLETED",
        )
        completed_count: int = (await db.execute(completed_count_stmt)).scalar() or 0

        has_incomplete_no_wip_batch = await _has_incomplete_no_wip_batch(db, scenario.id)
        effective_total = total + (1 if has_incomplete_no_wip_batch else 0)

        progress_rate = round(completed_count / effective_total, 2) if effective_total > 0 else 0.0
        remaining_count = max(effective_total - completed_count, 0)

        has_incomplete_no_wip_batch = await _has_incomplete_no_wip_batch(db, scenario.id)
        effective_total = total + (1 if has_incomplete_no_wip_batch else 0)

        progress_rate = round(completed_count / effective_total, 2) if effective_total > 0 else 0.0
        remaining_count = max(effective_total - completed_count, 0)

        # ── 4. 현재 배치 집계 (ready / processing 기준) ──────────────────
        active_ready_batch = await _get_active_ready_batch(db, scenario.id)
        active_processing_state = await _get_active_processing_state(db, scenario.id)
        active_processing_batch = (
            active_processing_state["batch"] if active_processing_state else None
        )

        current_batch_remaining_count = 0
        current_batch_pending_inbound_count = 0
        requires_production_completion = False
        blocking_production_batch_id: Optional[int] = None

        current_focus_batch = active_ready_batch or active_processing_batch
        if current_focus_batch:
            current_batch_remaining_count = await _count_incomplete_items_in_batch(
                db, current_focus_batch.id
            )
        if active_processing_state:
            processing_context = active_processing_state["context"]
            if (
                active_processing_batch is not None
                and active_processing_batch.completed_at is None
                and not processing_context["has_output"]
            ):
                requires_production_completion = True
                blocking_production_batch_id = active_processing_batch.id
            if processing_context["cutting_done"] and processing_context["has_output"]:
                current_batch_pending_inbound_count = len(
                    processing_context["linked_pending_entries"]
                )

        # ── 5. 생산 준비 대상 배치 그룹 (RELOCATE/PICKING만 포함) ────────
        all_batches_stmt = (
            select(Batch)
            .where(Batch.scenario_id == scenario.id)
            .order_by(Batch.batch_order.asc())
        )
        all_batches = (await db.execute(all_batches_stmt)).scalars().all()

        batch_groups: list[FieldBatchGroup] = []
        for batch in all_batches:
            group = await _build_batch_group(db, batch, exclude_completed=True)
            if group.relocation or group.picking:
                batch_groups.append(group)

        ordered_items = await _get_ordered_field_items_for_scenario(
            db,
            scenario,
            exclude_completed=True,
        )

        result.append(
            FieldReadyData(
                scenarioId=scenario.id,
                scenarioTitle=scenario.title,
                lazerName=(
                    scenario.lazer_name.value
                    if hasattr(scenario.lazer_name, "value")
                    else scenario.lazer_name
                ),
                scenarioProgressRate=progress_rate,
                completedTaskCount=completed_count,
                totalTaskCount=total,
                remainingTaskCount=remaining_count,
                currentBatchRemainingTaskCount=current_batch_remaining_count,
                currentBatchPendingInboundCount=current_batch_pending_inbound_count,
                requiresProductionCompletion=requires_production_completion,
                blockingProductionBatchId=blocking_production_batch_id,
                orderedItems=ordered_items,
                batch=batch_groups,
                nextScenarioId=next_scenario.id if next_scenario else None,
                nextScenarioTitle=next_scenario.title if next_scenario else None,
            )
        )

    return result

# ═══════════════════════════════════════════════════════════════════════
# QR 인식 화면 — GET (relocQr / pickingQr / inboundQr)
# ═══════════════════════════════════════════════════════════════════════

async def _get_lazer_name_for_batch(db: AsyncSession, batch_id: int) -> Optional[str]:
    """Batch ID → Scenario.lazer_name 조회 헬퍼 (PICKING/INBOUND 위치 표시용)"""
    batch = await db.get(Batch, batch_id)
    if not batch:
        return None
    scenario = await db.get(Scenarios, batch.scenario_id)
    return scenario.lazer_name if scenario else None


async def _get_qr_scan_data(
    db: AsyncSession,
    batch_item_id: int,
    action_type: str,
) -> Optional[QrScanData]:
    """
    GET QR 인식 화면 3종 공통 조회 헬퍼.

    action_type 에 따라 from/to 위치 이름 결정 방식이 달라진다.
      - RELOCATE : from = from_location.loc_name  /  to = to_location.loc_name
      - PICKING  : from = from_location.loc_name  /  to = scenario.lazer_name
      - INBOUND  : from = scenario.lazer_name     /  to = to_location.loc_name
    """
    item = await db.get(BatchItems, batch_item_id)
    if not item:
        return None

    wip, estimated_wip, _ = await _resolve_batch_item_material_source(db, item)
    lc = await _resolve_batch_item_lazer_cutting(db, item)
    lazer_name = await _get_lazer_name_for_batch(db, item.batch_id)

    is_raw_material = (estimated_wip is None) and ((wip is None) or (wip.qr_id is None))

    if action_type == "INBOUND":
        from_loc_name: Optional[str] = lazer_name
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        to_loc_name: Optional[str] = to_loc.loc_name if to_loc else None
    elif action_type == "PICKING":
        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        from_loc_name = from_loc.loc_name if from_loc else None
        to_loc_name = lazer_name
    else:  # RELOCATE
        from_loc = await db.get(Locations, item.from_location) if item.from_location else None
        from_loc_name = from_loc.loc_name if from_loc else None
        to_loc = await db.get(Locations, item.to_location) if item.to_location else None
        to_loc_name = to_loc.loc_name if to_loc else None

    if estimated_wip:
        manufacturer = estimated_wip.manufacturer
        material = estimated_wip.material
        thickness = estimated_wip.thickness
        width = estimated_wip.width
        height = estimated_wip.length
        weight = estimated_wip.weight
    elif wip:
        manufacturer = wip.manufacturer if wip else ""
        material = wip.material if wip else ""
        thickness = wip.thickness if wip else 0.0
        width = wip.width if wip else 0.0
        height = wip.length if wip else 0.0
        weight = wip.weight if wip else 0.0
    else:
        manufacturer, material, thickness, width, height, weight = _build_virtual_input_source(lc)

    return QrScanData(
        batchItemId=item.id,
        wipId=item.steel_wip_id or (wip.id if wip else 0),
        manufacturer=manufacturer or "",
        material=material,
        thickness=thickness or 0.0,
        width=width or 0.0,
        height=height or 0.0,
        weight=weight or 0.0,
        fromLocationName=from_loc_name,
        toLocationName=to_loc_name,
        # ▼ 수정: 원자재면 itemScan을 True로 고정 (스캔 단계 없음)
        itemScan=True if is_raw_material else (item.item_scanned_at is not None),
        destinationScan=item.destination_scanned_at is not None,
    )


async def get_reloc_qr(db: AsyncSession, batch_item_id: int) -> list:
    """GET /api/field/{batchItemId}/relocQr — 재배치 QR 화면 조회"""
    data = await _get_qr_scan_data(db, batch_item_id, "RELOCATE")
    if data is None:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")
    return [data]


async def get_picking_qr(db: AsyncSession, batch_item_id: int) -> list:
    """GET /api/field/{batchItemId}/pickingQr — 피킹 QR 화면 조회"""
    data = await _get_qr_scan_data(db, batch_item_id, "PICKING")
    if data is None:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")
    return [data]


async def get_inbound_qr(db: AsyncSession, batch_item_id: int) -> list:
    """GET /api/field/{batchItemId}/inboundQr — 적재 QR 화면 조회"""
    data = await _get_qr_scan_data(db, batch_item_id, "INBOUND")
    if data is None:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")
    return [data]


# ═══════════════════════════════════════════════════════════════════════
# QR 인식 화면 — POST (wipQR / locQR / save)
# ═══════════════════════════════════════════════════════════════════════

async def scan_wip_qr(db: AsyncSession, batch_item_id: int, req: WipQrRequest) -> None:
    """
    POST /api/field/{batchItemId}/wipQR — 잔재 QR 스캔.

    Poka-Yoke: 스캔된 QR 코드가 해당 batchItem의 steel_wip과 일치하는지 검증한다.
    통과 시 batch_item.item_scanned_at을 현재 시각으로 기록한다.
    """
    item = await db.get(BatchItems, batch_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")

    qr_stmt = select(QrCodes).where(QrCodes.qr_code == req.wipQr)
    qr = (await db.execute(qr_stmt)).scalars().first()
    if not qr:
        raise HTTPException(status_code=400, detail="등록되지 않은 QR 코드입니다.")

    wip_stmt = select(SteelWip).where(SteelWip.qr_id == qr.id)
    wip = (await db.execute(wip_stmt)).scalars().first()

    if not wip or wip.id != item.steel_wip_id:
        raise HTTPException(status_code=400, detail="스캔된 QR이 작업 대상 잔재와 일치하지 않습니다.")

    item.item_scanned_at = datetime.now(timezone.utc)
    await db.commit()


async def scan_loc_qr(db: AsyncSession, batch_item_id: int, req: LocQrRequest) -> None:
    """
    POST /api/field/{batchItemId}/locQR — 위치 QR 스캔.

    Poka-Yoke: 스캔된 위치명이 해당 batchItem의 to_location과 일치하는지 검증한다.
    PICKING은 to_location=null(레이저 기기)이므로 위치 검증을 생략한다.
    통과 시 batch_item.destination_scanned_at을 현재 시각으로 기록한다.
    """
    item = await db.get(BatchItems, batch_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")

    target_loc = await db.get(Locations, item.to_location) if item.to_location else None
    is_virtual_buffer_target = bool(
        target_loc is not None and (target_loc.loc_name or "").strip().upper() == "BUF-1"
    )

    if is_virtual_buffer_target:
        item.destination_scanned_at = datetime.now(timezone.utc)
        await db.commit()
        return

    if req.qrAction != "PICKING":
        loc_stmt = select(Locations).where(Locations.loc_name == req.locQr)
        loc = (await db.execute(loc_stmt)).scalars().first()
        if not loc:
            raise HTTPException(status_code=400, detail="등록되지 않은 위치 QR입니다.")
        if loc.id != item.to_location:
            raise HTTPException(status_code=400, detail="스캔된 위치가 작업 목표 위치와 일치하지 않습니다.")

    item.destination_scanned_at = datetime.now(timezone.utc)
    await db.commit()


async def save_qr_action(db: AsyncSession, batch_item_id: int, req: QrSaveRequest) -> QrSaveResult:
    """
    POST /api/field/{batchItemId} — 저장 버튼 클릭, 작업 완료 처리.

    wipQR / locQR가 제공되면 Poka-Yoke 재검증 후 완료 처리한다.
    QR 값이 없으면(mock 스캔 모드) 검증을 건너뛰고 바로 완료 처리한다.

    완료 처리 시:
      - batch_item.status = COMPLETED
      - RELOCATION : steel_wip.location_id = to_location
      - INBOUND    : steel_wip.location_id = to_location, steel_wip.status = IN_STOCK
      - PICKING    : steel_wip.location_id = None (레이저 투입 → 창고 위치 해제)
    """
    item = await db.get(BatchItems, batch_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")

    if item.status == "COMPLETED":
        remaining_after_complete = await _count_incomplete_items_in_batch(db, item.batch_id)
        pending_inbound_after_complete = await _count_incomplete_items_in_batch(
            db,
            item.batch_id,
            action="INBOUND",
        )
        return QrSaveResult(
            batchItemId=item.id,
            action=item.batch_item_action,
            currentBatchRemainingTaskCount=remaining_after_complete,
            currentBatchPendingInboundCount=pending_inbound_after_complete,
            shouldMoveToReady=remaining_after_complete == 0,
        )

    # batch_item_action으로 작업 유형 자동 판단 (RELOCATE / PICKING / INBOUND)
    action = item.batch_item_action

    # wipQR 검증 (QR 값이 제공된 경우에만)
    wip = None
    if item.steel_wip_id is not None:
        wip = await db.get(SteelWip, item.steel_wip_id)
        if req.wipQR:
            qr_stmt = select(QrCodes).where(QrCodes.qr_code == req.wipQR)
            qr = (await db.execute(qr_stmt)).scalars().first()
            if not qr:
                raise HTTPException(status_code=400, detail="등록되지 않은 잔재 QR 코드입니다.")
            wip_stmt = select(SteelWip).where(SteelWip.qr_id == qr.id)
            validated_wip = (await db.execute(wip_stmt)).scalars().first()
            if not validated_wip or validated_wip.id != item.steel_wip_id:
                raise HTTPException(status_code=400, detail="스캔된 잔재 QR이 작업 대상과 일치하지 않습니다.")

    # locQR 검증 (QR 값이 제공된 경우에만, PICKING은 목적지가 레이저 기기이므로 생략)
    target_loc = await db.get(Locations, item.to_location) if item.to_location else None
    is_virtual_buffer_target = bool(
        action == "RELOCATE"
        and target_loc is not None
        and (target_loc.loc_name or "").strip().upper() == "BUF-1"
    )
    if req.locQR and action in ("RELOCATE", "INBOUND") and not is_virtual_buffer_target:
        loc_stmt = select(Locations).where(Locations.loc_name == req.locQR)
        loc = (await db.execute(loc_stmt)).scalars().first()
        if not loc or loc.id != item.to_location:
            raise HTTPException(status_code=400, detail="스캔된 위치가 작업 목표 위치와 일치하지 않습니다.")

    # 완료 처리 — 스캔 타임스탬프 기록 + 상태 변경
    # 프론트 mock 스캔 플로우에서는 QR 성공 여부를 로컬 state로만 관리하므로,
    # wipQR/locQR가 비어 있더라도 저장 자체는 허용하고 현재 시각으로 스캔 완료 처리한다.
    now = datetime.now(timezone.utc)
    item.item_scanned_at = item.item_scanned_at or now
    item.destination_scanned_at = item.destination_scanned_at or now
    item.status = "COMPLETED"

    estimated_wip = None
    if action == "INBOUND" and item.estimated_wip_id is not None:
        estimated_wip = await db.get(EstimatedWips, item.estimated_wip_id)

    if action == "INBOUND" and wip is None and estimated_wip is not None:
        wip = SteelWip(
            status=SteelWipStatus.IN_STOCK.value,
            manufacturer=estimated_wip.manufacturer,
            material=estimated_wip.material or "",
            thickness=estimated_wip.thickness or 0.0,
            width=estimated_wip.width or 0.0,
            length=estimated_wip.length or 0.0,
            weight=estimated_wip.weight or 0.0,
            location_id=item.to_location,
            stack_level=None,
            qr_id=estimated_wip.qr_id,
        )
        db.add(wip)
        await db.flush()
        item.steel_wip_id = wip.id

    is_direct_start = _is_direct_start_item(
        action=action,
        wip=wip,
        item=item,
        to_loc=target_loc,
    )

    if wip is not None:
        if req.thickness is not None:
            wip.thickness = req.thickness
        if req.width is not None:
            wip.width = req.width
        if req.length is not None:
            wip.length = req.length
        
        if action == "RELOCATE":
            if is_direct_start:
                wip.location_id = None
                wip.stack_level = None
                wip.status = "CONSUMED"
            else:
                wip.location_id = item.to_location
                if item.to_location is not None:
                    wip.stack_level = await _get_next_stack_level(db, item.to_location)
        elif action == "INBOUND":
            wip.location_id = item.to_location
            wip.status = "IN_STOCK"
            if item.to_location is not None:
                wip.stack_level = await _get_next_stack_level(db, item.to_location)
        elif action == "PICKING":
            wip.location_id = None
            wip.stack_level = None   # ← 레이저 투입 시 stack_level도 초기화
            wip.status = "CONSUMED"

    related_lc = await _resolve_batch_item_lazer_cutting(db, item)
    if related_lc is not None:
        action_str = action.value if hasattr(action, "value") else str(action)
        if action_str == "PICKING" or (action_str == "RELOCATE" and is_direct_start):
            related_lc.status = "IN_PROGRESS"

    await db.commit()

    # ✅ PICKING / DIRECT_START 완료 시: 앞선 no-WIP 배치를 자동 완료 처리
    #    "A 생산이 재공품 X & 다음 생산 피킹 완료 → A 생산은 완료로 간주"
    action_str = action.value if hasattr(action, "value") else str(action)
    if action_str == "PICKING" or (action_str == "RELOCATE" and is_direct_start):
        await _auto_complete_preceding_no_wip_batches(db, item.batch_id)

    remaining_after_complete = await _count_incomplete_items_in_batch(db, item.batch_id)
    pending_inbound_after_complete = await _count_incomplete_items_in_batch(
        db,
        item.batch_id,
        action="INBOUND",
    )

    # ✅ 모든 BatchItem이 완료되면 Batch 완료 처리 (completed_at 기록)
    # 모든 BatchItem이 COMPLETED가 됐을 때 자동 완료 처리
    # 단, 재공품이 없는 배치(INBOUND 아이템이 하나도 없는 경우)는
    # 작업자가 직접 "생산완료" 버튼을 눌러야 하므로 여기서 자동 완료하지 않는다.
    if remaining_after_complete == 0:
        # 해당 배치에 INBOUND 아이템이 하나라도 있는지 확인
        total_inbound_stmt = select(func.count(BatchItems.id)).where(
            BatchItems.batch_id == item.batch_id,
            BatchItems.batch_item_action == "INBOUND",
        )
        total_inbound = (await db.execute(total_inbound_stmt)).scalar() or 0

        if total_inbound > 0:
            # 재공품이 있는 배치 → 모든 INBOUND까지 완료됐으므로 자동 완료
            await _complete_batch(db, item.batch_id)
        # 재공품이 없는 배치 → 자동 완료하지 않음. complete_batch_manually()에서만 완료 처리.

    return QrSaveResult(
        batchItemId=item.id,
        action=action,
        currentBatchRemainingTaskCount=remaining_after_complete,
        currentBatchPendingInboundCount=pending_inbound_after_complete,
        shouldMoveToReady=remaining_after_complete == 0,
    )

# 재공품 없는 Batch의 수동 생산완료 처리
async def complete_batch_manually(db: AsyncSession, batch_id: int) -> dict:
    """
    재공품(lazer_cutting)이 없는 Batch에서 프론트의 '생산완료' 버튼 클릭 시 호출.
    1. 해당 Batch의 미완료 BatchItem(PICKING)을 모두 COMPLETED 처리
    2. PICKING에 연결된 SteelWip.status = CONSUMED
    3. Batch.completed_at 기록
    """
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="배치를 찾을 수 없습니다.")
    if batch.completed_at is not None:
        return {
            "scenarioId": batch.scenario_id,
            "nextStage": "end",
        }

    lc_stmt = (
        select(LazerCutting)
        .where(LazerCutting.batch_id == batch_id)
        .order_by(LazerCutting.id.asc())
    )
    lazer_cuttings = (await db.execute(lc_stmt)).scalars().all()
    pending_inbound_entries = await _get_pending_inbound_entries(db, batch_id)
    context = await _get_current_processing_context(
        db,
        batch,
        lazer_cuttings,
        pending_inbound_entries,
    )
    if context is None:
        raise HTTPException(status_code=400, detail="완료 처리할 현재 생산 작업을 찾을 수 없습니다.")

    if context["has_output"]:
        raise HTTPException(
            status_code=400,
            detail="발생 재공품이 있는 생산은 적재 완료 후 생산완료 처리할 수 있습니다.",
        )

    current_lc: LazerCutting = context["lc"]
    current_lc.status = "COMPLETED"

    incomplete_after_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch_id,
        BatchItems.status != "COMPLETED",
    )
    incomplete_after = (await db.execute(incomplete_after_stmt)).scalar() or 0
    if incomplete_after == 0:
        batch.completed_at = datetime.now(timezone.utc)

    await db.commit()

    active_processing_batch = await _get_active_processing_batch(db, batch.scenario_id)
    active_ready_batch = await _get_active_ready_batch(db, batch.scenario_id)

    next_stage = "end"
    if active_processing_batch is not None:
        next_stage = "processing"
    elif active_ready_batch is not None:
        next_stage = "ready"

    return {
        "scenarioId": batch.scenario_id,
        "nextStage": next_stage,
    }

async def complete_scenario(db: AsyncSession, scenario_id: int) -> None:
    """
    시나리오 작업 완료 처리.
    1. 대상 시나리오의 scenario_order를 0으로 변경 (완료됨 표시 → /app/start 미노출)
    2. 대상 시나리오보다 scenario_order가 큰 나머지 시나리오들을 1씩 감소
    3. 대상 시나리오 status를 COMPLETED, completed_at을 현재 시각으로 기록
    """
    from sqlalchemy import update

    target = await db.get(Scenarios, scenario_id)
    if not target:
        raise HTTPException(status_code=404, detail="시나리오를 찾을 수 없습니다.")

    # 이미 완료 처리된 경우 멱등성 보장 (재호출 시 오류 없이 반환)
    if target.scenario_order == 0:
        return

    # 대상보다 순서가 뒤인 시나리오들의 scenario_order를 1씩 감소
    await db.execute(
        update(Scenarios)
        .where(Scenarios.scenario_order > target.scenario_order)
        .values(scenario_order=Scenarios.scenario_order - 1)
    )

    # 대상 시나리오 완료 처리
    target.scenario_order = 0
    target.status = "COMPLETED"
    target.completed_at = datetime.now(timezone.utc)

    await db.commit()
