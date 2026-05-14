# app/algorithms/dummy_optimizer.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
import random

from app.models import (
    LazerCutting, Batch, BatchItems, SteelWip,
    Scenarios, EstimatedWips
)
from app.schemas.enums import BatchActionType, BatchItemStatus, WipStatus

RAW_MATERIAL_SIZES = {
    (2438, 6096),
    (2437, 12192),
    (6096, 2438),
    (12192, 2438),
}

RELOCATE_TIME_MIN = 5
RELOCATE_TIME_MAX = 10
INBOUND_TIME_MIN = 5
INBOUND_TIME_MAX = 10


def is_raw_material(wip: SteelWip) -> bool:
    return (round(wip.width), round(wip.length)) in RAW_MATERIAL_SIZES


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

    # 기존 배치/배치아이템 삭제 (중복 생성 방지)
    existing_batches_stmt = select(Batch).where(Batch.scenario_id == scenario_id)
    existing_batches = (await db.execute(existing_batches_stmt)).scalars().all()
    for batch in existing_batches:
        items_stmt = select(BatchItems).where(BatchItems.batch_id == batch.id)
        items = (await db.execute(items_stmt)).scalars().all()
        for item in items:
            await db.delete(item)
        await db.delete(batch)
    await db.flush()

    stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
    result = await db.execute(stmt)
    cuttings: List[LazerCutting] = list(result.scalars().all())

    if not cuttings:
        return

    # 상단 상수 부분에 PICKING_TIME 추가
    RELOCATE_TIME_MIN = 5
    RELOCATE_TIME_MAX = 10
    PICKING_TIME_MIN = 5      # ← 추가
    PICKING_TIME_MAX = 10     # ← 추가
    INBOUND_TIME_MIN = 5
    INBOUND_TIME_MAX = 10

    BATCH_SIZE = 4
    grouped_cuttings = [
        cuttings[i:i + BATCH_SIZE] for i in range(0, len(cuttings), BATCH_SIZE)
    ]

    batch_order = 1
    for group in grouped_cuttings:
        new_batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
        db.add(new_batch)
        await db.flush()

        temp_batch_items = []
        current_time = 0          # ← 배치마다 0부터 시작
        picking_dest_idx = 0
        already_picked = set()

        for cut in group:
            cut.batch_id = new_batch.id

            if not cut.steel_wip_id:
                continue

            target_wip = await db.get(SteelWip, cut.steel_wip_id)
            if not target_wip or target_wip.status != WipStatus.IN_STOCK.value:
                raise ValueError(
                    f"WIP ID {cut.steel_wip_id}가 IN_STOCK 상태가 아닙니다."
                )
            if not target_wip.location_id:
                continue

            if target_wip.id not in already_picked:
                already_picked.add(target_wip.id)

                if is_raw_material(target_wip):
                    # ===== 원자재: 바로 피킹 =====
                    picking_dest = PICKING_DESTINATIONS[
                        picking_dest_idx % len(PICKING_DESTINATIONS)
                    ]
                    picking_dest_idx += 1
                    picking_time = random.randint(PICKING_TIME_MIN, PICKING_TIME_MAX)  # ← 변경
                    temp_batch_items.append({
                        "steel_wip_id": target_wip.id,
                        "action": BatchActionType.PICKING.value,
                        "from": target_wip.location_id,
                        "to": picking_dest,
                        "start_time": current_time,
                        "run_time": picking_time,
                    })
                    current_time += picking_time

                else:
                    # ===== 재공품: 피킹 부분 =====
                    picking_dest = PICKING_DESTINATIONS[
                        picking_dest_idx % len(PICKING_DESTINATIONS)
                    ]
                    picking_dest_idx += 1
                    picking_time = random.randint(PICKING_TIME_MIN, PICKING_TIME_MAX)  # ← 변경
                    temp_batch_items.append({
                        "steel_wip_id": target_wip.id,
                        "action": BatchActionType.PICKING.value,
                        "from": target_wip.location_id,
                        "to": picking_dest,
                        "start_time": current_time,
                        "run_time": picking_time,
                    })
                    current_time += picking_time

            # ── 적재(INBOUND): EstimatedWips가 있는 경우에만 ──
            est_wips_stmt = select(EstimatedWips).where(
                EstimatedWips.lazer_cutting_id == cut.id
            )
            est_wips = (await db.execute(est_wips_stmt)).scalars().all()

            for est_wip in est_wips:
                new_steel_wip = SteelWip(
                    status=WipStatus.REGISTERED.value,
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

                # 적재는 해당 커팅의 피킹 완료 후 cutting_time이 지난 시점
                inbound_start_time = current_time + (cut.estimated_cutting_time or 0)
                inbound_time = random.randint(INBOUND_TIME_MIN, INBOUND_TIME_MAX)
                inbound_dest = get_inbound_target_location(ALL_LOCATION_IDS)
                temp_batch_items.append({
                    "steel_wip_id": new_steel_wip.id,
                    "action": BatchActionType.INBOUND.value,
                    "from": None,
                    "to": inbound_dest,
                    "start_time": inbound_start_time,
                    "run_time": inbound_time,
                })

        temp_batch_items.sort(key=lambda x: x["start_time"])

        for order, item in enumerate(temp_batch_items):
            db.add(BatchItems(
                batch_id=new_batch.id,
                steel_wip_id=item["steel_wip_id"],
                batch_item_order=order,
                batch_item_action=item["action"],
                status=BatchItemStatus.BEFORE_PENDING.value,
                from_location=item["from"],
                to_location=item["to"],
                expected_start_time=item["start_time"],
                expected_running_time=item["run_time"],
            ))

        batch_order += 1

    await db.commit()