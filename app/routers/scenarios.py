# app/routers/scenarios.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.scenario import ScenarioCreateRequest, ScenarioResponse
from app.services import scenario_service

# app/routers/scenarios.py
from typing import List
from app.schemas.scenario import ScenarioResultData

router = APIRouter()

@router.post("/create", response_model=BaseResponse[ScenarioResponse])
async def create_scenario(
    request: ScenarioCreateRequest, 
    db: AsyncSession = Depends(get_db)
):
    try:
        scenario = await scenario_service.get_or_create_scenario(
            db=db, 
            project_id=request.project_id, 
            scenario_due=request.scenario_due
        )
        return BaseResponse(
            status=201,
            message="시나리오가 생성(또는 조회)되었습니다.",
            data=scenario
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    

# 1. 시나리오 결과 확인 (GET)
@router.get("/{scenario_id}", response_model=BaseResponse[List[ScenarioResultData]])
async def get_scenario_detail(scenario_id: int, db: AsyncSession = Depends(get_db)):
    data = await scenario_service.get_scenario_result(db, scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오 결과 조회에 성공했습니다.",
        data=data
    )


# 3. 시나리오 취소/삭제 (DELETE)
@router.delete("/{scenario_id}", response_model=BaseResponse)
async def delete_scenario(scenario_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await scenario_service.delete_scenario_cascade(db, scenario_id)
        return BaseResponse(
            status=200,
            message="시나리오 및 관련 데이터가 모두 삭제되었습니다.",
            data=None
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))