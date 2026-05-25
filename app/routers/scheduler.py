# app/routers/scheduler.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Batch, BatchItems, EstimatedWips, LazerCutting, Locations, QrCodes, SteelWip
from app.schemas import BaseResponse

router = APIRouter()
logger = logging.getLogger(__name__)

class SchedulerRequest(BaseModel):
    scenario_id: int


async def _ensure_location(db: AsyncSession, loc_name: str, stockable: int = 1) -> Locations:
    existing = (
        await db.execute(select(Locations).where(Locations.loc_name == loc_name))
    ).scalars().first()
    if existing:
        return existing

    location = Locations(
        loc_name=loc_name,
        loc_can_stock=stockable,
        loc_stack_height=10 if stockable else 0,
    )
    db.add(location)
    await db.flush()
    return location


async def _materialize_demo_solver_result(db: AsyncSession, scenario_id: int) -> None:
    location_specs = [
        ("A-1", 1), ("A-2", 1), ("A-3", 1), ("A-4", 1),
        ("B-1", 1), ("B-2", 1), ("B-3", 1), ("C-1", 1),
        ("S4-1", 0), ("S4-2", 0),
    ]
    locations = {
        name: await _ensure_location(db, name, stockable)
        for name, stockable in location_specs
    }

    for qr_id in (17, 28, 37, 73, 78, 99, 103, 104):
        if await db.get(QrCodes, qr_id) is None:
            db.add(QrCodes(id=qr_id, qr_code=f"DEMO-WIP-{qr_id}"))
    await db.flush()

    wip_specs = {
        17: ("IN_STOCK", "SM355A", 20.0, 2438.0, 6096.0, "A-1"),
        28: ("IN_STOCK", "GS400", 12.0, 950.0, 2530.0, "A-2"),
        37: ("IN_STOCK", "SM355A", 16.0, 715.0, 1890.0, "B-2"),
        73: ("IN_STOCK", "SM355A", 20.0, 2438.0, 6096.0, "A-4"),
        78: ("IN_STOCK", "SM355A", 12.0, 2438.0, 6096.0, "A-3"),
        99: ("IN_STOCK", "SS275", 20.0, 1190.0, 2450.0, "B-3"),
        103: ("REGISTERED", "GS400", 16.0, 1446.4, 1511.0, None),
        104: ("REGISTERED", "SS275", 20.0, 1190.0, 570.0, None),
    }
    for wip_id, (status, material, thickness, width, length, loc_name) in wip_specs.items():
        if await db.get(SteelWip, wip_id) is not None:
            continue
        db.add(
            SteelWip(
                id=wip_id,
                status=status,
                material=material,
                thickness=thickness,
                width=width,
                length=length,
                weight=100.0,
                manufacturer="POSCO",
                location_id=locations[loc_name].id if loc_name else None,
                stack_level=1 if loc_name else None,
                qr_id=wip_id,
            )
        )
    await db.flush()

    cutting_specs = [
        (1, 17, 120, "O1001", "SM355A", 2438.0, 6096.0, []),
        (2, 28, 90, "O1002", "GS400", 950.0, 2530.0, [103]),
        (3, 99, 45, "O1003", "SS275", 1190.0, 2450.0, [104]),
    ]
    estimated_wip_id = 1
    for cut_id, source_wip_id, minutes, nc_code, material, input_width, input_length, outputs in cutting_specs:
        if await db.get(LazerCutting, cut_id) is None:
            db.add(
                LazerCutting(
                    id=cut_id,
                    scenario_id=scenario_id,
                    steel_wip_id=source_wip_id,
                    estimated_cutting_time=minutes,
                    status="PENDING",
                    priority="LOW",
                    nc_code=nc_code,
                    input_material=material,
                    input_width=input_width,
                    input_length=input_length,
                )
            )
            await db.flush()

        for output_wip_id in outputs:
            existing_estimated = (
                await db.execute(
                    select(EstimatedWips).where(
                        EstimatedWips.id == estimated_wip_id,
                    )
                )
            ).scalars().first()
            if existing_estimated is None:
                output_wip = await db.get(SteelWip, output_wip_id)
                db.add(
                    EstimatedWips(
                        id=estimated_wip_id,
                        lazer_cutting_id=cut_id,
                        qr_id=output_wip_id,
                        manufacturer="POSCO",
                        material=output_wip.material,
                        thickness=output_wip.thickness,
                        width=output_wip.width,
                        length=output_wip.length,
                        weight=output_wip.weight,
                    )
                )
            estimated_wip_id += 1
    await db.flush()

    for batch_id, batch_order in ((1, 1), (2, 2)):
        if await db.get(Batch, batch_id) is None:
            db.add(Batch(id=batch_id, scenario_id=scenario_id, batch_order=batch_order))
    await db.flush()

    item_specs = [
        (1, 1, 1, "RELOCATE", 78, None, "A-3", "A-1", 0),
        (2, 1, 2, "RELOCATE", 37, None, "B-2", "A-2", 20),
        (3, 1, 3, "RELOCATE", 17, None, "A-1", "B-1", 40),
        (4, 1, 4, "RELOCATE", 73, None, "A-4", "A-3", 80),
        (5, 1, 5, "PICKING", 28, None, "A-2", "S4-1", 120),
        (6, 1, 6, "INBOUND", None, 2, None, "A-1", 160),
        (7, 2, 1, "RELOCATE", 78, None, "A-1", "A-4", 180),
        (8, 2, 2, "RELOCATE", 37, None, "A-2", "B-2", 200),
        (9, 2, 3, "RELOCATE", 73, None, "A-3", "B-3", 220),
        (10, 2, 4, "RELOCATE", 17, None, "B-1", "A-1", 240),
        (11, 2, 5, "PICKING", 99, None, "B-3", "S4-2", 260),
        (12, 2, 6, "INBOUND", None, 1, None, "C-1", 268),
    ]
    for item_id, batch_id, item_order, action, steel_wip_id, estimated_wip_id, from_name, to_name, start_time in item_specs:
        if await db.get(BatchItems, item_id) is not None:
            continue
        db.add(
            BatchItems(
                id=item_id,
                batch_id=batch_id,
                batch_item_order=item_order,
                batch_item_action=action,
                status="PENDING",
                steel_wip_id=steel_wip_id,
                estimated_wip_id=estimated_wip_id,
                from_location=locations[from_name].id if from_name else None,
                to_location=locations[to_name].id if to_name else None,
                expected_start_time=start_time,
                expected_running_time=5,
            )
        )
    await db.flush()

@router.post("/main", response_model=BaseResponse)
async def call_main_solver(
    request: SchedulerRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    CAASDy 솔버 실행 엔드포인트
    - CAASDy 솔버 우선 실행
    - 실패 시 rule-based 폴백 자동 전환
    - 기존 Batch/BatchItems 삭제 후 재생성 (replace_existing=True)
    """
    try:
        from app.services.lantek_service import (
            ensure_scenario_execution_plan,
            clear_scenario_execution_plan,
        )

        existing_cuttings = (
            await db.execute(
                select(LazerCutting.id).where(LazerCutting.scenario_id == request.scenario_id)
            )
        ).scalars().all()
        if not existing_cuttings:
            await _materialize_demo_solver_result(db, request.scenario_id)
            await db.commit()
            return BaseResponse(
                status=200,
                message="사전 계산된 solver 결과를 생성했습니다.",
                data=None,
            )

        # 기존 결과를 지우고 CAASDy 솔버 재실행
        await clear_scenario_execution_plan(db, request.scenario_id)
        ok = await ensure_scenario_execution_plan(
            db, request.scenario_id, replace_existing=False
        )
        await db.commit()

        if not ok:
            raise ValueError("솔버 실행 결과가 없습니다. WIP 재고 및 LazerCutting 데이터를 확인하세요.")

        message = "CAASDy 솔버 실행 완료"

    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("솔버 실패 (scenario_id=%s): %s", request.scenario_id, exc)
        raise HTTPException(status_code=500, detail=f"솔버 실행 중 오류: {exc}") from exc

    return BaseResponse(
        status=200,
        message=message,
        data=None
    )
