# app/routers/field.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.field import FieldBatchItem
from app.services import field_service

router = APIRouter()

# 엔드포인트: /api/live_field/{lazer_name} (명세서 요구사항)
@router.get("/{lazer_name}", response_model=BaseResponse[List[FieldBatchItem]])
async def get_live_field_dashboard(
    lazer_name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    실시간 현장 정보 조회
    - 특정 레이저(lazer_name)에 할당된 진행 중인 시나리오의 첫 번째 Batch 안에서,
    - PENDING, IN_PROGRESS 상태인 작업 지시(BatchItem)만 시간순으로 필터링하여 반환합니다.
    """
    data = await field_service.get_live_field_data(db, lazer_name)
    
    return BaseResponse(
        status=200,
        message="현장 실시간 정보 조회에 성공했습니다.",
        data=data
    )