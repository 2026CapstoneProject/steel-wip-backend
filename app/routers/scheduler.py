# app/routers/scheduler.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.algorithms.dummy_optimizer import run_asis_optimization

router = APIRouter()
logger = logging.getLogger(__name__)

class SchedulerRequest(BaseModel):
    scenario_id: int

@router.post("/main", response_model=BaseResponse)
async def call_main_solver(
    request: SchedulerRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    AS-IS SOLVER 호출
    - 원자재: planned_source_wip_id 기반 바로 피킹
    - 재공품: steel_wip_id 기반 위 자재 재배치 후 피킹
    - 잔재 발생 시 생산 예상시간 이후 적재
    """
    try:
        await run_asis_optimization(db, request.scenario_id)
        message = "AS-IS Solver 실행 완료"
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "AS-IS solver failed for scenario %s", request.scenario_id
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BaseResponse(
        status=200,
        message=message,
        data=None
    )