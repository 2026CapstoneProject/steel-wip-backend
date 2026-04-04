# app/routers/scenario_send.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.services import scenario_service

router = APIRouter()

@router.post("/{scenario_id}", response_model=BaseResponse)
async def send_scenario_to_field_endpoint(
    scenario_id: int, 
    db: AsyncSession = Depends(get_db)
):
    """
    시나리오 현장 전송
    - 선택된 시나리오를 ORDERED 상태로 변경하고, 작업 지시를 PENDING으로 활성화합니다.
    - 동일한 이름(title)으로 생성되었으나 선택받지 못한 다른 비교군 시나리오들은 DB에서 영구 삭제됩니다.
    """
    try:
        await scenario_service.send_scenario_to_field(db, scenario_id)
        return BaseResponse(
            status=200,
            message="시나리오가 성공적으로 현장에 전송되었습니다.",
            data=None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))