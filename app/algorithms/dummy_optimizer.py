# app/algorithms/dummy_optimizer.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import random

from app.models import LazerCutting, Batch, BatchItems, SteelWip
from app.schemas.enums import BatchActionType, BatchItemStatus

# --- 구역 그룹 매핑 함수 ---
def get_relocate_target_location(current_loc_id: int) -> int:
    """같은 구역 그룹 내에서 이동할 다른 위치를 무작위 반환"""
    if current_loc_id in [1, 2, 3, 4]:
        # 현재 위치를 제외한 나머지 구역 중 하나 선택
        return random.choice([loc for loc in [1, 2, 3, 4] if loc != current_loc_id] or [current_loc_id])
    elif current_loc_id in [5, 6, 7]:
        return random.choice([loc for loc in [5, 6, 7] if loc != current_loc_id] or [current_loc_id])
    elif current_loc_id in [8, 9]:
        return random.choice([loc for loc in [8, 9] if loc != current_loc_id] or [current_loc_id])
    return current_loc_id # 규칙 외의 구역은 제자리 이동

async def run_dummy_optimization(db: AsyncSession, scenario_id: int):
    # 1. 시나리오에 할당된 모든 LazerCutting 조회
    stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
    result = await db.execute(stmt)
    cuttings: List[LazerCutting] = list(result.scalars().all())

    if not cuttings:
        return

    # 4개씩 묶기 위한 설정
    BATCH_SIZE = 4
    grouped_cuttings = [cuttings[i:i + BATCH_SIZE] for i in range(0, len(cuttings), BATCH_SIZE)]
    
    # 1배치 내 피킹 도착지 후보 (id 15~18)
    PICKING_DESTINATIONS = [15, 16, 17, 18]

    batch_order = 1
    for group in grouped_cuttings:
        # 2. 새로운 Batch 생성
        new_batch = Batch(
            scenario_id=scenario_id,
            batch_order=batch_order
        )
        db.add(new_batch)
        await db.flush() # new_batch.id 발급
        
        batch_item_order = 1
        picking_dest_idx = 0 # 15~18번을 순차적으로 할당하기 위한 인덱스
        
        # 3. 각 커팅 작업에 대해 BatchItems 생성
        for cut in group:
            cut.batch_id = new_batch.id 
            
            if not cut.steel_wip_id:
                continue
                
            target_wip = await db.get(SteelWip, cut.steel_wip_id)
            if not target_wip or not target_wip.location_id:
                continue
                
            # [재배치] 현재 자재보다 위층(stack_level)에 있는 자재들 조회
            top_wips_stmt = select(SteelWip).where(
                SteelWip.location_id == target_wip.location_id,
                SteelWip.stack_level > target_wip.stack_level
            ).order_by(SteelWip.stack_level.desc())
            
            top_wips_result = await db.execute(top_wips_stmt)
            top_wips = top_wips_result.scalars().all()
            
            # (1) 재배치(RELOCATE) BatchItem 생성
            for top_wip in top_wips:
                relocate_dest = get_relocate_target_location(top_wip.location_id)
                
                relocate_item = BatchItems(
                    batch_id=new_batch.id,
                    steel_wip_id=top_wip.id,
                    batch_item_order=batch_item_order,
                    batch_item_action=BatchActionType.RELOCATE.value, 
                    status=BatchItemStatus.BEFORE_PENDING.value,
                    from_location=top_wip.location_id,
                    to_location=relocate_dest, # 그룹 내의 다른 구역으로 도착지 할당
                    expected_start_time=batch_item_order * 5,
                    expected_running_time=5
                )
                db.add(relocate_item)
                batch_item_order += 1
                
            # (2) 본 작업인 피킹(PICKING) BatchItem 생성
            # 15~18번(S4-1 ~ S4-4) 중 하나를 순서대로 할당 (4개를 초과할 경우 인덱스 순환)
            picking_dest = PICKING_DESTINATIONS[picking_dest_idx % len(PICKING_DESTINATIONS)]
            picking_dest_idx += 1
            
            picking_item = BatchItems(
                batch_id=new_batch.id,
                steel_wip_id=target_wip.id,
                batch_item_order=batch_item_order,
                batch_item_action=BatchActionType.PICKING.value, 
                status=BatchItemStatus.BEFORE_PENDING.value,     
                from_location=target_wip.location_id,
                to_location=picking_dest, # 15~18번 중 하나 할당
                expected_start_time=batch_item_order * 5,
                expected_running_time=10
            )
            db.add(picking_item)
            batch_item_order += 1
            
        batch_order += 1

    # 최종 DB 반영
    await db.commit()