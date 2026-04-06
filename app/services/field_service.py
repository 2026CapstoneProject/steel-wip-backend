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
)
from app.schemas.field import FieldBatchItem, FieldWipDetail
from app.schemas.enums import BatchItemStatus


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
    1. 현재 진행 중인 시나리오 (최소 scenario_order)를 먼저 확인한다.
    2. 전달받은 batch_id가 그 시나리오에 속하는지 검증한다.
    3. 시나리오 전체 진행률(완료 아이템 / 전체 아이템)을 계산한다.
    4. 해당 시나리오의 Batch 중 '완료된 Batch'(모든 아이템 COMPLETED)만 리턴한다.

    * 명세서의 GET + Request Body 구조는 HTTP 표준에 맞지 않아
      Query Parameter(?batchId=...)로 대체한다.

    * 현재 시나리오는 최소 scenario_order를 가진 시나리오 (일반적으로 1).
    """

    # 1. 현재 진행 중인 시나리오 (최소 scenario_order) 조회
    # scenario_order는 시나리오 큐의 순서 → 최소값이 "현재 활성" 시나리오
    scenario_stmt = (
        select(Scenarios)
        .order_by(Scenarios.scenario_order.asc())
    )
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


async def get_live_field_data(db: AsyncSession, lazer_name: str) -> List[FieldBatchItem]:
    # 1. 진행 중인(ORDERED, IN_PROGRESS) 시나리오들 조회 (해당 레이저 담당)
    scenario_stmt = select(Scenarios.id).where(
        Scenarios.lazer_name == lazer_name,
        Scenarios.status.in_(["ORDERED", "IN_PROGRESS"])
    )
    scenario_ids = (await db.execute(scenario_stmt)).scalars().all()
    
    if not scenario_ids:
        return []

    # 2. 해당 시나리오들의 첫 번째 Batch(batch_order == 1) ID들 조회
    batch_stmt = select(Batch.id).where(
        Batch.scenario_id.in_(scenario_ids),
        Batch.batch_order == 1
    )
    batch_ids = (await db.execute(batch_stmt)).scalars().all()
    
    if not batch_ids:
        return []

    # 3. 해당 Batch에 속한 BatchItems 중 PENDING 또는 IN_PROGRESS 상태인 것들 시간순 조회
    item_stmt = (
        select(BatchItems)
        .where(
            BatchItems.batch_id.in_(batch_ids),
            BatchItems.status.in_([BatchItemStatus.PENDING.value, BatchItemStatus.IN_PROGRESS.value])
        )
        .order_by(BatchItems.expected_start_time.asc())
    )
    items_result = await db.execute(item_stmt)
    batch_items = items_result.scalars().all()

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

        # 6. 스키마에 맞게 조립
        response_list.append(FieldBatchItem(
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
    scenario_stmt = select(Scenarios).order_by(Scenarios.scenario_order.asc())
    scenario = (await db.execute(scenario_stmt)).scalars().first()
    if not scenario:
        return []

    # ── 2. 첫 번째 배치 (최소 batch_order) ──────────────────────────────
    batch_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order.asc())
    )
    batch = (await db.execute(batch_stmt)).scalars().first()
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
                wipStatus=actual_wip.status,
                wipName=wip_name,
                toLocation=to_loc.loc_name if to_loc else None,
                status=item_status,
            ))

        lc_groups.append(ProgressLazerCutting(
            lazerCuttingId=lc.id,
            inputWipId=input_wip_id,
            material=material,
            wip=wip_items,
        ))

    return [FieldProgressData(
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
       - 완료되지 않은 Batch 중 첫 번째(생산 중 화면 담당) → 제외
       - 나머지 미완료 Batch들만 생산 준비 대상으로 포함
    3. 각 Batch의 RELOCATE / PICKING 아이템을 분리해 FieldBatchGroup으로 변환한다.
       INBOUND(적재) 아이템은 제외한다. (기존 _build_batch_group 헬퍼 재사용)
    4. 진행률(scenarioProgressRate) = 현재 시나리오 전체 batch_item 중 COMPLETED 비율
       (생산 중 배치 포함 전체 시나리오 기준)
    5. 다음 시나리오가 없으면 nextScenarioId / nextScenarioTitle 은 None으로 반환한다.
    """

    # ── 1. 현재 시나리오 (최소 scenario_order) ──────────────────────────
    scenario_stmt = select(Scenarios).order_by(Scenarios.scenario_order.asc())
    scenario = (await db.execute(scenario_stmt)).scalars().first()
    if not scenario:
        return []

    # ── 2. 다음 시나리오 (두 번째로 작은 scenario_order) ─────────────────
    next_scenario_stmt = (
        select(Scenarios)
        .order_by(Scenarios.scenario_order.asc())
        .offset(1)
        .limit(1)
    )
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

    # ── 5. 생산 준비 대상 Batch 필터링 ──────────────────────────────────────
    # batch_order 순으로 전체 배치를 순회하며:
    #   - 완료된 배치(모든 아이템 COMPLETED)       → 건너뜀
    #   - 미완료 배치 중 첫 번째(현재 생산 중)     → 건너뜀 (생산 중 화면 담당)
    #   - 나머지 미완료 배치                       → 생산 준비 대상
    all_batches_stmt = (
        select(Batch)
        .where(Batch.scenario_id == scenario.id)
        .order_by(Batch.batch_order)
    )
    all_batches = (await db.execute(all_batches_stmt)).scalars().all()

    in_progress_skipped = False   # 생산 중 배치를 한 번만 건너뜀
    batch_groups: list[FieldBatchGroup] = []

    for batch in all_batches:
        # 완료된 배치 제외
        if await _is_batch_completed(db, batch.id):
            continue
        # 미완료 배치 중 첫 번째 = 생산 중 화면 담당 → 건너뜀
        if not in_progress_skipped:
            in_progress_skipped = True
            continue
        # 나머지 미완료 배치 → FieldBatchGroup 변환
        group = await _build_batch_group(db, batch)
        batch_groups.append(group)

    return [
        FieldReadyData(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            scenarioProgressRate=progress_rate,
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
        material=wip.material if wip else "",
        thickness=wip.thickness if wip else 0.0,
        width=wip.width if wip else 0.0,
        height=wip.length if wip else 0.0,   # DB 컬럼명=length, 명세서 표기=height
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


async def save_qr_action(db: AsyncSession, batch_item_id: int, req: QrSaveRequest) -> None:
    """
    POST /api/field/{batchItemId} — 저장 버튼 클릭, 작업 완료 처리.

    wipQR / locQR 재검증 후:
      - batch_item.status = COMPLETED
      - RELOCATION : steel_wip.location_id = to_location
      - INBOUND    : steel_wip.location_id = to_location, steel_wip.status = IN_STOCK
      - PICKING    : steel_wip.location_id = None (레이저 투입 → 창고 위치 해제)
    """
    item = await db.get(BatchItems, batch_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="배치 아이템을 찾을 수 없습니다.")

    # wipQR 재검증
    qr_stmt = select(QrCodes).where(QrCodes.qr_code == req.wipQR)
    qr = (await db.execute(qr_stmt)).scalars().first()
    if not qr:
        raise HTTPException(status_code=400, detail="등록되지 않은 잔재 QR 코드입니다.")

    wip_stmt = select(SteelWip).where(SteelWip.qr_id == qr.id)
    wip = (await db.execute(wip_stmt)).scalars().first()
    if not wip or wip.id != item.steel_wip_id:
        raise HTTPException(status_code=400, detail="스캔된 잔재 QR이 작업 대상과 일치하지 않습니다.")

    # batch_item_action으로 작업 유형 자동 판단 (RELOCATE / PICKING / INBOUND)
    action = item.batch_item_action

    # locQR 검증 (PICKING은 목적지가 레이저 기기이므로 생략)
    if action in ("RELOCATE", "INBOUND"):
        loc_stmt = select(Locations).where(Locations.loc_name == req.locQR)
        loc = (await db.execute(loc_stmt)).scalars().first()
        if not loc or loc.id != item.to_location:
            raise HTTPException(status_code=400, detail="스캔된 위치 QR이 작업 목표 위치와 일치하지 않습니다.")

    # 완료 처리 — 스캔 타임스탬프 기록 + 상태 변경
    now = datetime.now(timezone.utc)
    item.item_scanned_at = now
    item.destination_scanned_at = now
    item.status = "COMPLETED"

    if action == "RELOCATE":
        wip.location_id = item.to_location
    elif action == "INBOUND":
        wip.location_id = item.to_location
        wip.status = "IN_STOCK"
    elif action == "PICKING":
        wip.location_id = None

    await db.commit()
