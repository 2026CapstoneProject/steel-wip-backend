# app/routers/scheduler.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse

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
