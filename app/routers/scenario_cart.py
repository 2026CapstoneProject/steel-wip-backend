# app/routers/scenario_cart.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import date

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.scenario import ProjectScenarioHistory
from app.services import scenario_service

router = APIRouter()

@router.get("", response_model=BaseResponse[List[ProjectScenarioHistory]])
async def get_scenario_cart(
    projectName: Optional[str] = Query(None, description="프로젝트 명 검색"),
    scenarioName: Optional[str] = Query(None, description="시나리오 명 검색"),
    projDueMin: Optional[date] = Query(None, description="프로젝트 납기 최소일"),
    projDueMax: Optional[date] = Query(None, description="프로젝트 납기 최대일"),
    scenDueMin: Optional[date] = Query(None, description="시나리오 납기 최소일"),
    scenDueMax: Optional[date] = Query(None, description="시나리오 납기 최대일"),
    genDateMin: Optional[date] = Query(None, description="시나리오 생성 최소일"),
    genDateMax: Optional[date] = Query(None, description="시나리오 생성 최대일"),
    db: AsyncSession = Depends(get_db)
):
    data = await scenario_service.get_scenario_history(
        db=db,
        project_name=projectName,
        scenario_name=scenarioName,
        proj_due_min=projDueMin,
        proj_due_max=projDueMax,
        scen_due_min=scenDueMin,
        scen_due_max=scenDueMax,
        gen_date_min=genDateMin,
        gen_date_max=genDateMax
    )
    
    return BaseResponse(
        status=200,
        message="시나리오 생성 이력 조회에 성공했습니다.",
        data=data
    )