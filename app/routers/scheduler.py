# app/routers/scheduler.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.services.demo_solver_service import materialize_demo_solver_result

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
    MAIN_SOLVER 호출
    - 실제 solver는 시연 환경에서 시간이 오래 걸리므로,
      사전 계산된 solver 결과를 DB에 반영한다.
    """
    try:
        summary = await materialize_demo_solver_result(db, request.scenario_id)
        await db.commit()
        message = (
            "사전 계산된 solver 결과를 반영했습니다. "
            f"(Batch {summary['batchCount']}건, 작업 {summary['taskCount']}건, "
            f"발생 재공품 {summary['generatedWipCount']}건)"
        )
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "Demo solver materialization failed for scenario %s",
            request.scenario_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BaseResponse(
        status=200,
        message=message,
        data=None
    )
