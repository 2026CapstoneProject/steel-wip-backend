# app/services/field_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.models import Scenarios, Batch, BatchItems, SteelWip, Locations, QrCodes
from app.schemas.field import FieldBatchItem, FieldWipDetail
from app.schemas.enums import BatchItemStatus

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