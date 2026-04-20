# app/services/field_service.py
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException

from app.models import Scenarios, Batch, BatchItems, SteelWip, Locations, QrCodes, LazerCutting, EstimatedWips
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


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────

async def _build_batch_group(
    db: AsyncSession,
    batch: Batch,
    exclude_completed: bool = False,
    only_completed: bool = False,
) -> FieldBatchGroup:
    """
    Batch 하나를 받아 재배치 / 피킹 / 적재 목록으로 분리한 FieldBatchGroup을 반환한다.
    batch_item_order 기준으로 정렬.

    exclude_completed=True이면 COMPLETED 상태 아이템을 제외한다.
    (생산 준비 화면에서 완료된 개별 아이템을 숨기기 위함)

    only_completed=True이면 COMPLETED 상태 아이템만 포함한다.
    (작업 완료 화면에서 완료된 아이템만 보여주기 위함)
    """
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
                expectedRunningTime=item.expected_running_time or 0,
                # 원자재(wipId == 0)인 경우에만 규격 필드를 채운다
                thickness=wip.thickness if wip else None,
                width=wip.width if wip else None,
                height=wip.length if wip else None,  # DB length → height 매핑
            ))
        elif item.batch_item_action == "INBOUND":
            inbound_items.append(InboundBatchItem(
                batchItemId=item.id,
                wipId=wip.id if wip else 0,
                material=wip.material if wip else "",
                fromLocationName=from_loc.loc_name if from_loc else None,
                toLocationName=to_loc.loc_name if to_loc else None,
                expectedRunningTime=item.expected_running_time or 0,
                thickness=wip.thickness if wip else None,
                width=wip.width if wip else None,
                height=wip.length if wip else None,
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
            ["RELOCATE", "PICKING"],
        )
        if pending_ready_count > 0:
            return batch

        pending_total = await _count_incomplete_items_in_batch(db, batch.id)
        if pending_total > 0:
            # 현재 배치가 생산 중(INBOUND만 남음)이면 뒤 배치는 아직 ready에 노출하지 않는다.
            return None

    return None


async def _get_active_processing_batch(db: AsyncSession, scenario_id: int) -> Optional[Batch]:
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
            ["RELOCATE", "PICKING"],
        )
        if pending_ready_count > 0:
            return None

        pending_total = await _count_incomplete_items_in_batch(db, batch.id)
        if pending_total == 0:
            continue

        pending_inbound_count = await _count_incomplete_items_in_batch(
            db,
            batch.id,
            action="INBOUND",
        )
        if pending_inbound_count > 0:
            return batch

    return None


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

    progress_rate = round(completed_count / total, 2) if total > 0 else 0.0
    remaining_count = max(total - completed_count, 0)

    # 4. 배치 전체가 완료된 경우에만 완료 목록에 포함한다.
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
        group = await _build_batch_group(db, batch, only_completed=True)
        if not group.relocation and not group.picking and not group.inbound:
            continue
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
        )
        .order_by(BatchItems.batch_id.asc(), BatchItems.batch_item_order.asc(), BatchItems.expected_start_time.asc())
    )
    batch_items = (await db.execute(item_stmt)).scalars().all()

    response_list = []
    
    for item in batch_items:
        wip_detail_list = []
        
        # 4. WIP 데이터 및 QR 코드 조회
        if item.steel_wip_id:
            wip = await db.get(SteelWip, item.steel_wip_id)
            if wip:
                qr_code_val = "UNKNOWN"
                if wip.qr_id:
                    qr = await db.get(QrCodes, wip.qr_id)
                    if qr:
                        qr_code_val = qr.qr_code
                
                # float 값들을 명세서 형식인 str로 변환
                wip_detail_list.append(FieldWipDetail(
                    qrId=qr_code_val,
                    material=wip.material or "",
                    manufacturer=wip.manufacturer or "",
                    thickness=str(wip.thickness) if wip.thickness else "0",
                    width=str(wip.width) if wip.width else "0",
                    length=str(wip.length) if wip.length else "0",
                    weight=str(wip.weight) if wip.weight else "0"
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


# ─────────────────────────────────────────────
# GET /api/field/progress  —  생산 중 화면
# ─────────────────────────────────────────────

def _fmt_dim(v: float | None) -> str:
    """치수 숫자를 wipName 문자열용으로 변환. 정수면 소수점 제거."""
    if v is None:
        return "0"
    return str(int(v)) if v == int(v) else str(v)


async def get_field_progress(db: AsyncSession) -> list:
    """
    생산 중 화면
    1. 현재 시나리오(최소 scenario_order)를 조회한다.
    2. 해당 시나리오의 첫 번째 배치(최소 batch_order)를 조회한다.
    3. 해당 배치의 lazer_cutting 목록을 조회한다.
    4. 각 lazer_cutting에 연결된 estimated_wips를 조회하고,
       qr_id를 통해 실제 steel_wip과 INBOUND batch_item 상태를 결합한다.
    5. expectedTotalRunningTime = lazer_cutting.estimated_cutting_time 합산 (분)
    """

    # ── 1. 현재 시나리오 (최소 scenario_order) ──────────────────────────
    scenario = await _get_current_active_scenario(db)
    if not scenario:
        return []

    # ── 2. 현재 생산 중 배치 ─────────────────────────────────────────────
    # RELOCATE/PICKING이 모두 끝나고 INBOUND만 남은 배치만 생산 중으로 본다.
    batch = await _get_active_processing_batch(db, scenario.id)
    if not batch:
        return []

    # ── 3. 해당 배치의 lazer_cutting 목록 ───────────────────────────────
    lc_stmt = (
        select(LazerCutting)
        .where(LazerCutting.batch_id == batch.id)
        .order_by(LazerCutting.id)
    )
    lazer_cuttings = (await db.execute(lc_stmt)).scalars().all()
    if not lazer_cuttings:
        return []

    # ── 4. 총 예상 소요 시간 (분) ────────────────────────────────────────
    expected_total = sum(lc.estimated_cutting_time or 0 for lc in lazer_cuttings)
    batch_total_stmt = select(func.count(BatchItems.id)).where(BatchItems.batch_id == batch.id)
    batch_total = (await db.execute(batch_total_stmt)).scalar() or 0
    batch_completed_stmt = select(func.count(BatchItems.id)).where(
        BatchItems.batch_id == batch.id,
        BatchItems.status == "COMPLETED",
    )
    batch_completed = (await db.execute(batch_completed_stmt)).scalar() or 0
    batch_progress_rate = round(batch_completed / batch_total, 2) if batch_total > 0 else 0.0
    batch_remaining = max(batch_total - batch_completed, 0)

    # ── 5. 각 lazer_cutting별 데이터 구성 ───────────────────────────────
    lc_groups: list[ProgressLazerCutting] = []

    for lc in lazer_cuttings:
        # 투입 재공품 정보
        input_wip = await db.get(SteelWip, lc.steel_wip_id) if lc.steel_wip_id else None
        input_wip_id = input_wip.id if input_wip else 0
        material = input_wip.material if input_wip else ""

        # 해당 lazer_cutting의 estimated_wips 조회
        ew_stmt = (
            select(EstimatedWips)
            .where(EstimatedWips.lazer_cutting_id == lc.id)
        )
        ew_list = (await db.execute(ew_stmt)).scalars().all()

        wip_items: list[ProgressWipItem] = []

        for ew in ew_list:
            # qr_id → 실제 steel_wip 조회
            wip_stmt = select(SteelWip).where(SteelWip.qr_id == ew.qr_id)
            actual_wip = (await db.execute(wip_stmt)).scalars().first()
            if not actual_wip:
                continue

            # 같은 배치의 INBOUND batch_item 조회 (적재 대상)
            inbound_stmt = select(BatchItems).where(
                BatchItems.batch_id == batch.id,
                BatchItems.steel_wip_id == actual_wip.id,
                BatchItems.batch_item_action == "INBOUND",
            )
            inbound_item = (await db.execute(inbound_stmt)).scalars().first()

            # to_location 이름
            to_loc = None
            if inbound_item and inbound_item.to_location:
                to_loc = await db.get(Locations, inbound_item.to_location)

            # 상태 표시 변환
            item_status = ""
            if inbound_item:
                if inbound_item.status == "COMPLETED":
                    item_status = "적재 완료"
                elif inbound_item.status == "IN_PROGRESS":
                    item_status = "적재 대기"
                else:
                    # PENDING / BEFORE_PENDING: 아직 시작 전
                    item_status = inbound_item.status

            # wipName: "{두께}X{가로}X{세로}"
            wip_name = (
                f"{_fmt_dim(actual_wip.thickness)}"
                f"X{_fmt_dim(actual_wip.width)}"
                f"X{_fmt_dim(actual_wip.length)}"
            )

            wip_items.append(ProgressWipItem(
                wipId=actual_wip.id,
                batchItemId=inbound_item.id if inbound_item else None,
                wipStatus=actual_wip.status,
                wipName=wip_name,
                toLocation=to_loc.loc_name if to_loc else None,
                status=item_status,
            ))

        lc_groups.append(ProgressLazerCutting(
            lazerCuttingId=lc.id,
            inputWipId=input_wip_id,
            material=material,
            estimatedCuttingTime=lc.estimated_cutting_time or 0,
            wip=wip_items,
        ))

    return [FieldProgressData(
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        batchProgressRate=batch_progress_rate,
        completedTaskCount=batch_completed,
        totalTaskCount=batch_total,
        remainingTaskCount=batch_remaining,
        expectedTotalRunningTime=expected_total,
        lazer_cutting=lc_groups,
    )]


# ─────────────────────────────────────────────
# GET /api/field/ready  —  생산 준비 화면
# ─────────────────────────────────────────────

async def get_field_ready(db: AsyncSession) -> list:
    """
    생산 준비 화면
    1. 현재 시나리오(최소 scenario_order)와 다음 시나리오(두 번째로 작은 scenario_order)를 조회한다.
    2. 현재 시나리오의 모든 Batch를 batch_order 순으로 순회하며 아래 규칙을 적용한다.
       - 이미 완료된 Batch(모든 아이템 COMPLETED) → 제외
       - 미완료 Batch의 RELOCATE / PICKING 작업을 생산 준비 대상으로 포함
       - INBOUND(적재) 작업은 제외
    3. 각 Batch의 RELOCATE / PICKING 아이템을 분리해 FieldBatchGroup으로 변환한다.
       INBOUND(적재) 아이템은 제외한다. (기존 _build_batch_group 헬퍼 재사용)
    4. 진행률(scenarioProgressRate) = 현재 시나리오 전체 batch_item 중 COMPLETED 비율
       (생산 중 배치 포함 전체 시나리오 기준)
    5. 다음 시나리오가 없으면 nextScenarioId / nextScenarioTitle 은 None으로 반환한다.
    """

    # ── 1. 현재 시나리오 (최소 scenario_order) ──────────────────────────
    scenario = await _get_current_active_scenario(db)
    if not scenario:
        return []

    # ── 2. 다음 시나리오 (두 번째로 작은 scenario_order) ─────────────────
    next_scenario_stmt = select(Scenarios).order_by(Scenarios.scenario_order.asc()).offset(1).limit(1)
    next_scenario = (await db.execute(next_scenario_stmt)).scalars().first()

    # ── 3. 현재 시나리오의 모든 Batch ID 수집 ────────────────────────────
    all_batch_ids_stmt = select(Batch.id).where(Batch.scenario_id == scenario.id)
    all_batch_ids: list[int] = (await db.execute(all_batch_ids_stmt)).scalars().all()

    # ── 4. 진행률 계산 ────────────────────────────────────────────────────
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
    remaining_count = max(total - completed_count, 0)

    # ── 5. 생산 준비 대상 배치 결정 ───────────────────────────────────────
    # 정의:
    #   - 생산 준비 페이지에서는 현재 시나리오의 모든 미완료 RELOCATE/PICKING 작업을 보여준다.
    #   - 즉, 각 배치의 ready 작업을 batch_order 순으로 모두 노출한다.
    #   - INBOUND만 남은 작업은 생산 중 단계이므로 ready 목록에는 포함하지 않는다.
    active_ready_batch = await _get_active_ready_batch(db, scenario.id)
    active_processing_batch = await _get_active_processing_batch(db, scenario.id)

    batch_groups: list[FieldBatchGroup] = []
    current_batch_remaining_count = 0
    current_batch_pending_inbound_count = 0

    current_focus_batch = active_ready_batch or active_processing_batch
    if current_focus_batch:
        current_batch_remaining_count = await _count_incomplete_items_in_batch(
            db,
            current_focus_batch.id,
        )
        current_batch_pending_inbound_count = await _count_incomplete_items_in_batch(
            db,
            current_focus_batch.id,
            action="INBOUND",
        )

    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order.asc())
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    for batch in all_batches:
        group = await _build_batch_group(db, batch, exclude_completed=True)
        if group.relocation or group.picking:
            batch_groups.append(group)

    return [
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
            batch=batch_groups,
            nextScenarioId=next_scenario.id if next_scenario else None,
            nextScenarioTitle=next_scenario.title if next_scenario else None,
        )
    ]


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

    wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
    lazer_name = await _get_lazer_name_for_batch(db, item.batch_id)

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

    return QrScanData(
        batchItemId=item.id,
        wipId=wip.id if wip else 0,
        manufacturer=wip.manufacturer if wip else "",
        material=wip.material if wip else "",
        thickness=wip.thickness if wip else 0.0,
        width=wip.width if wip else 0.0,
        height=wip.length if wip else 0.0,   # DB 컬럼명=length, 명세서 표기=height
        weight=wip.weight if wip else 0.0,
        fromLocationName=from_loc_name,
        toLocationName=to_loc_name,
        itemScan=item.item_scanned_at is not None,
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
    if req.locQR and action in ("RELOCATE", "INBOUND"):
        loc_stmt = select(Locations).where(Locations.loc_name == req.locQR)
        loc = (await db.execute(loc_stmt)).scalars().first()
        if not loc or loc.id != item.to_location:
            raise HTTPException(status_code=400, detail="스캔된 위치 QR이 작업 목표 위치와 일치하지 않습니다.")

    # 완료 처리 — 스캔 타임스탬프 기록 + 상태 변경
    # 프론트 mock 스캔 플로우에서는 QR 성공 여부를 로컬 state로만 관리하므로,
    # wipQR/locQR가 비어 있더라도 저장 자체는 허용하고 현재 시각으로 스캔 완료 처리한다.
    now = datetime.now(timezone.utc)
    item.item_scanned_at = item.item_scanned_at or now
    item.destination_scanned_at = item.destination_scanned_at or now
    item.status = "COMPLETED"

    if wip is not None:
        if action == "RELOCATE":
            wip.location_id = item.to_location
        elif action == "INBOUND":
            wip.location_id = item.to_location
            wip.status = "IN_STOCK"
        elif action == "PICKING":
            wip.location_id = None

    await db.commit()
    remaining_after_complete = await _count_incomplete_items_in_batch(db, item.batch_id)
    pending_inbound_after_complete = await _count_incomplete_items_in_batch(
        db,
        item.batch_id,
        action="INBOUND",
    )
    return QrSaveResult(
        batchItemId=item.id,
        action=action,
        currentBatchRemainingTaskCount=remaining_after_complete,
        currentBatchPendingInboundCount=pending_inbound_after_complete,
        shouldMoveToReady=remaining_after_complete == 0,
    )
