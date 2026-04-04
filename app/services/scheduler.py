# app/routers/scheduler.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.algorithms.dummy_optimizer import run_dummy_optimization

from fastapi import HTTPException

router = APIRouter()

class SchedulerRequest(BaseModel):
    scenario_id: int

@router.post("/main", response_model=BaseResponse)
async def call_main_solver(
    request: SchedulerRequest, 
    db: AsyncSession = Depends(get_db)
):
    try:
        await run_dummy_optimization(db, request.scenario_id)
        return BaseResponse(
            status=200,
            message="최적화 알고리즘 구동이 완료되었습니다.",
            data=None
        )
    except ValueError as e:
        # IN_STOCK이 아니면 400 에러를 던집니다.
        raise HTTPException(status_code=400, detail=str(e))