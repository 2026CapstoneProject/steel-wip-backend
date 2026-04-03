# app/routers/scheduler.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.algorithms.dummy_optimizer import run_dummy_optimization

router = APIRouter()

class SchedulerRequest(BaseModel):
    scenario_id: int

@router.post("/main", response_model=BaseResponse)
async def call_main_solver(
    request: SchedulerRequest, 
    db: AsyncSession = Depends(get_db)
):
    """
    MAIN_SOLVER 호출
    - 시나리오에 할당된 작업을 기반으로 Batch와 BatchItems(이동 동선)를 생성합니다.
    """
    # 더미 최적화 알고리즘 구동 (DB 업데이트 포함)
    await run_dummy_optimization(db, request.scenario_id)
    
    return BaseResponse(
        status=200,
        message="최적화 알고리즘 구동이 완료되었습니다.",
        data=None
    )