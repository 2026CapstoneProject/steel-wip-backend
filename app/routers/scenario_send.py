from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import date

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.scenario import SentProjectHistory
from app.services import scenario_service

router = APIRouter()

# 1. 시나리오 현장 전송 이력 조회 (GET) - 경로: /api/scenario_send/
@router.get("/", response_model=BaseResponse[List[SentProjectHistory]])
async def get_sent_scenario_history(
    projectName: Optional[str] = Query(None, description="프로젝트 명 검색"),
    projDueMin: Optional[date] = Query(None, description="프로젝트 납기 최소일"),
    projDueMax: Optional[date] = Query(None, description="프로젝트 납기 최대일"),
    scenDueMin: Optional[date] = Query(None, description="시나리오 납기 최소일"),
    scenDueMax: Optional[date] = Query(None, description="시나리오 납기 최대일"),
    sendDateMin: Optional[date] = Query(None, description="현장 전송(발행) 최소일"),
    sendDateMax: Optional[date] = Query(None, description="현장 전송(발행) 최대일"),
    db: AsyncSession = Depends(get_db)
):
    data = await scenario_service.get_sent_scenario_history(
        db=db,
        project_name=projectName,
        proj_due_min=projDueMin,
        proj_due_max=projDueMax,
        scen_due_min=scenDueMin,
        scen_due_max=scenDueMax,
        send_date_min=sendDateMin,
        send_date_max=sendDateMax
    )
    
    return BaseResponse(
        status=200,
        message="현장 전송 정보 조회에 성공했습니다.",
        data=data
    )

# 2. 시나리오 현장 전송 (POST)
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