# app/routers/scenarios.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.scenario import ScenarioCreateRequest, ScenarioResponse
from app.services import scenario_service

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