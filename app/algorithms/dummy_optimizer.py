# app/algorithms/dummy_optimizer.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from typing import List
import random

from app.models import (
    LazerCutting, Batch, BatchItems, SteelWip,
    Scenarios, EstimatedWips, Locations,
    BatchItemsBatchItemAction, BatchItemsStatus, SteelWipStatus, QrCodes
)

# 원자재 판별 기준 (width x length, mm 단위)
RAW_MATERIAL_SIZES = {
    (2438, 6096),
    (2437, 12192),
    (6096, 2438),
    (12192, 2438),
}

def is_raw_material(width: float, length: float) -> bool:
    return (int(width), int(length)) in RAW_MATERIAL_SIZES

def get_relocate_target_location(current_loc_id: int, all_location_ids: list) -> int:
    others = [loc for loc in all_location_ids if loc != current_loc_id]
    return others[0] if others else current_loc_id

def get_inbound_target_location(all_location_ids: list) -> int:
    return random.choice(all_location_ids) if all_location_ids else 1


async def run_asis_optimization(db: AsyncSession, scenario_id: int):
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")

    scenario.status = "DRAFT"
    # ── 기존 Batch/BatchItems 삭제 (중복 방지) ──────────────────────
    existing_batch_ids = (
        await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))
    ).scalars().all()

    if existing_batch_ids:
        await db.execute(delete(BatchItems).where(BatchItems.batch_id.in_(existing_batch_ids)))
        await db.execute(delete(Batch).where(Batch.id.in_(existing_batch_ids)))

    # ── 기존 EstimatedWips에서 REGISTERED 상태로 생성된 SteelWip 삭제 ──
    cutting_ids = (
        await db.execute(select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id))
    ).scalars().all()

    if cutting_ids:
        qr_ids = (
            await db.execute(
                select(EstimatedWips.qr_id).where(
                    EstimatedWips.lazer_cutting_id.in_(cutting_ids),
                    EstimatedWips.qr_id.is_not(None),
                )
            )
        ).scalars().all()

        if qr_ids:
            await db.execute(
                delete(SteelWip).where(
                    SteelWip.qr_id.in_(qr_ids),
                    SteelWip.status == SteelWipStatus.REGISTERED.value,
                )
            )
    # ───────────────────────────────────────────────────────────────

    stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
    result = await db.execute(stmt)
    cuttings: List[LazerCutting] = list(result.scalars().all())

    if not cuttings:
        await db.commit()
        return

    # ── DB에서 실제 location 목록 조회 ──────────────────────────────
    loc_result = await db.execute(select(Locations))
    all_locations = loc_result.scalars().all()

    # loc_can_stock=1: 재공품 보관 가능 (재배치/적재 대상)
    stock_location_ids = [loc.id for loc in all_locations if loc.loc_can_stock == 1]
    # loc_can_stock=0: 레이저 앞 대기 등 피킹 목적지
    picking_dest_ids = [loc.id for loc in all_locations if loc.loc_can_stock == 0]

    # fallback: 구분이 없을 경우 전체 사용
    if not stock_location_ids:
        stock_location_ids = [loc.id for loc in all_locations]
    if not picking_dest_ids:
        picking_dest_ids = stock_location_ids
    # ───────────────────────────────────────────────────────────────

    BATCH_SIZE = 4
    grouped_cuttings = [cuttings[i:i + BATCH_SIZE] for i in range(0, len(cuttings), BATCH_SIZE)]

    batch_order = 1
    for group in grouped_cuttings:
        new_batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
        db.add(new_batch)
        await db.flush()

        temp_batch_items = []
        current_time = 0
        picking_dest_idx = 0

        for cut in group:
            cut.batch_id = new_batch.id

            if cut.steel_wip_id is None:
                # ==============================
                # 원자재 경로
                # steel_wip_id 없음 = 원자재
                # ==============================
                picking_dest = picking_dest_ids[picking_dest_idx % len(picking_dest_ids)]
                picking_dest_idx += 1

                temp_batch_items.append({
                    "steel_wip_id": None,
                    "action": BatchItemsBatchItemAction.PICKING.value,
                    "from": None,
                    "to": picking_dest,
                    "start_time": current_time,
                    "run_time": 10,
                    "note": f"원자재 피킹 | {cut.input_material} {int(cut.input_width or 0)}x{int(cut.input_length or 0)} | NC: {cut.nc_code}",
                })
                current_time += 10

            else:
                # ==============================
                # 재공품 경로
                # steel_wip_id 있음 = 재공품
                # 위에 쌓인 것들 재배치 후 피킹
                # ==============================
                target_wip = await db.get(SteelWip, cut.steel_wip_id)
                if not target_wip:
                    raise ValueError(f"WIP ID {cut.steel_wip_id}를 찾을 수 없습니다.")
                if target_wip.status != SteelWipStatus.IN_STOCK.value:
                    raise ValueError(f"WIP ID {cut.steel_wip_id}가 IN_STOCK 상태가 아닙니다. (현재: {target_wip.status})")
                if not target_wip.location_id:
                    raise ValueError(f"WIP ID {cut.steel_wip_id}의 location이 없습니다.")

                # 같은 location에서 더 높은 stack_level에 있는 WIP들 재배치
                top_wips_stmt = select(SteelWip).where(
                    and_(
                        SteelWip.location_id == target_wip.location_id,
                        SteelWip.stack_level > target_wip.stack_level,
                        SteelWip.status == SteelWipStatus.IN_STOCK.value
                    )
                ).order_by(SteelWip.stack_level.desc())
                top_wips = (await db.execute(top_wips_stmt)).scalars().all()

                for top_wip in top_wips:
                    relocate_dest = get_relocate_target_location(
                        top_wip.location_id, stock_location_ids  # ← DB 조회 결과 사용
                    )
                    temp_batch_items.append({
                        "steel_wip_id": top_wip.id,
                        "action": BatchItemsBatchItemAction.RELOCATE.value,
                        "from": top_wip.location_id,
                        "to": relocate_dest,
                        "start_time": current_time,
                        "run_time": 5,
                    })
                    current_time += 5

                # 목표 재공품 피킹
                picking_dest = picking_dest_ids[picking_dest_idx % len(picking_dest_ids)]
                picking_dest_idx += 1
                temp_batch_items.append({
                    "steel_wip_id": target_wip.id,
                    "action": BatchItemsBatchItemAction.PICKING.value,
                    "from": target_wip.location_id,
                    "to": picking_dest,
                    "start_time": current_time,
                    "run_time": 10,
                })
                current_time += 10

            # ==============================
            # 적재(INBOUND): EstimatedWips가 있는 경우에만
            # ==============================
            est_wips_stmt = select(EstimatedWips).where(
                EstimatedWips.lazer_cutting_id == cut.id
            )
            est_wips_result = await db.execute(est_wips_stmt)
            est_wips = est_wips_result.scalars().all()

            for est_wip in est_wips:
                new_steel_wip = SteelWip(
                    status=SteelWipStatus.REGISTERED.value,
                    manufacturer=est_wip.manufacturer or "UNKNOWN",
                    material=est_wip.material or "UNKNOWN",
                    thickness=est_wip.thickness or 0.0,
                    width=est_wip.width or 0.0,
                    length=est_wip.length or 0.0,
                    weight=est_wip.weight or 0.0,
                    location_id=None,
                    stack_level=None,
                    qr_id=est_wip.qr_id,
                )
                db.add(new_steel_wip)
                await db.flush()

                inbound_start_time = current_time + (cut.estimated_cutting_time or 0)
                inbound_dest = get_inbound_target_location(stock_location_ids)  # ← DB 조회 결과 사용
                temp_batch_items.append({
                    "steel_wip_id": new_steel_wip.id,
                    "action": BatchItemsBatchItemAction.INBOUND.value,
                    "from": None,
                    "to": inbound_dest,
                    "start_time": inbound_start_time,
                    "run_time": 5,
                })

        # 배치 내 작업 정렬 후 저장
        temp_batch_items.sort(key=lambda x: x["start_time"])

        for order, item in enumerate(temp_batch_items):
            db.add(BatchItems(
                batch_id=new_batch.id,
                steel_wip_id=item["steel_wip_id"],
                batch_item_order=order,
                batch_item_action=item["action"],
                status=BatchItemsStatus.BEFORE_PENDING.value,
                from_location=item["from"],
                to_location=item["to"],
                expected_start_time=item["start_time"],
                expected_running_time=item["run_time"],
            ))

        batch_order += 1

    await db.commit()