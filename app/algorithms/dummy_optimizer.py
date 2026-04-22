# app/algorithms/dummy_optimizer.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import random

from app.models import (
    LazerCutting, Batch, BatchItems, SteelWip, 
    Scenarios, EstimatedWips, QrCodes
)
from app.schemas.enums import BatchActionType, BatchItemStatus, WipStatus

def get_relocate_target_location(current_loc_id: int) -> int:
    if current_loc_id in [1, 2, 3, 4]:
        return random.choice([loc for loc in [1, 2, 3, 4] if loc != current_loc_id] or [current_loc_id])
    elif current_loc_id in [5, 6, 7]:
        return random.choice([loc for loc in [5, 6, 7] if loc != current_loc_id] or [current_loc_id])
    elif current_loc_id in [8, 9]:
        return random.choice([loc for loc in [8, 9] if loc != current_loc_id] or [current_loc_id])
    return current_loc_id 

# --- 적재(INBOUND) 대상지 무작위 매핑 함수 ---
def get_inbound_target_location() -> int:
    """새로운 잔재를 적재할 빈 공간(id 1~9)을 무작위로 선택"""
    return random.choice([1, 2, 3, 4, 5, 6, 7, 8, 9])

async def run_dummy_optimization(db: AsyncSession, scenario_id: int):
    # 1. 시나리오 상태 확인
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")
    
    scenario.status = "DRAFT" 
    
    # 2. LazerCutting 조회
    stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
    result = await db.execute(stmt)
    cuttings: List[LazerCutting] = list(result.scalars().all())

    if not cuttings:
        return

    BATCH_SIZE = 4
    grouped_cuttings = [cuttings[i:i + BATCH_SIZE] for i in range(0, len(cuttings), BATCH_SIZE)]
    PICKING_DESTINATIONS = [15, 16, 17, 18]

    batch_order = 1
    for group in grouped_cuttings:
        # 3. 새로운 Batch 생성
        new_batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
        db.add(new_batch)
        await db.flush() 
        
        # 이번 Batch 안에서 생성할 모든 작업(BatchItem)을 임시 리스트에 담습니다.
        temp_batch_items = []
        
        # 1개의 작업을 할 때마다 증가하는 예상 시작 시간 (분 단위)
        current_time = 0
        picking_dest_idx = 0 
        
        # 4. 각 커팅(LazerCutting) 지시에 대한 재배치, 피킹 처리
        for cut in group:
            cut.batch_id = new_batch.id 
            
            if not cut.steel_wip_id:
                continue
                
            # [검증] IN_STOCK 상태 확인
            target_wip = await db.get(SteelWip, cut.steel_wip_id)
            if not target_wip or target_wip.status != WipStatus.IN_STOCK.value:
                raise ValueError(f"WIP ID {cut.steel_wip_id}가 IN_STOCK 상태가 아닙니다.")
                
            if not target_wip.location_id:
                continue
                
            # [잔재 생성] 예상 잔재를 SteelWip에 REGISTERED로 추가
            est_wips_stmt = select(EstimatedWips).where(EstimatedWips.lazer_cutting_id == cut.id)
            est_wips_result = await db.execute(est_wips_stmt)
            est_wips = est_wips_result.scalars().all()
            
            new_registered_wips = []
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
                    qr_id=est_wip.qr_id 
                )
                db.add(new_steel_wip)
                await db.flush() 
                new_registered_wips.append({
                    "wip_id": new_steel_wip.id,
                    "cutting_time": cut.estimated_cutting_time or 0
                })
            
            # [재배치] 위에 쌓인 철판들 이동 지시 생성
            top_wips_stmt = select(SteelWip).where(
                SteelWip.location_id == target_wip.location_id,
                SteelWip.stack_level > target_wip.stack_level
            ).order_by(SteelWip.stack_level.desc())
            top_wips = (await db.execute(top_wips_stmt)).scalars().all()
            
            for top_wip in top_wips:
                relocate_dest = get_relocate_target_location(top_wip.location_id)
                temp_batch_items.append({
                    "steel_wip_id": top_wip.id,
                    "action": BatchActionType.RELOCATE.value,
                    "from": top_wip.location_id,
                    "to": relocate_dest,
                    "start_time": current_time,
                    "run_time": 5
                })
                current_time += 5 # 재배치 소요 시간(5분) 누적
                
            # [피킹] 본 작업 철판 이동 지시 생성
            picking_dest = PICKING_DESTINATIONS[picking_dest_idx % len(PICKING_DESTINATIONS)]
            picking_dest_idx += 1
            
            temp_batch_items.append({
                "steel_wip_id": target_wip.id,
                "action": BatchActionType.PICKING.value,
                "from": target_wip.location_id,
                "to": picking_dest,
                "start_time": current_time,
                "run_time": 10
            })
            current_time += 10 # 피킹 소요 시간(10분) 누적
            
            # [적재] 아까 생성한 잔재(REGISTERED)를 INBOUND 하는 지시 생성
            # 피킹이 완료된 시점(current_time) + 해당 절단기의 절단 시간 = 적재 시작 시간
            for new_wip in new_registered_wips:
                inbound_start_time = current_time + new_wip["cutting_time"]
                inbound_dest = get_inbound_target_location()
                
                temp_batch_items.append({
                    "steel_wip_id": new_wip["wip_id"],
                    "action": BatchActionType.INBOUND.value,
                    "from": None, # 새 잔재이므로 출발지 없음
                    "to": inbound_dest,
                    "start_time": inbound_start_time,
                    "run_time": 5
                })

        # 5. 한 Batch 내의 모든 작업을 expected_start_time 기준으로 오름차순 정렬
        temp_batch_items.sort(key=lambda x: x["start_time"])
        
        # 6. 정렬된 순서대로 batch_item_order를 0부터 부여하며 DB 삽입
        for order, item in enumerate(temp_batch_items):
            db.add(BatchItems(
                batch_id=new_batch.id,
                steel_wip_id=item["steel_wip_id"],
                batch_item_order=order, # 0부터 시작
                batch_item_action=item["action"],
                status=BatchItemStatus.BEFORE_PENDING.value,
                from_location=item["from"],
                to_location=item["to"],
                expected_start_time=item["start_time"],
                expected_running_time=item["run_time"]
            ))
            
        batch_order += 1

    await db.commit()