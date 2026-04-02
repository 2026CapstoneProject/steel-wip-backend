# app/routers/lantek.py
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.lantek import LantekScenarioData, LantekDeleteRequest
from app.services import lantek_service

router = APIRouter()

# 1. 초기 화면 조회 (GET)
@router.get("/get/{scenario_id}", response_model=BaseResponse[List[LantekScenarioData]])
async def get_lantek_scenario(scenario_id: int, db: AsyncSession = Depends(get_db)):
    data = await lantek_service.get_lantek_data(db, scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오 정보 조회에 성공했습니다.",
        data=data
    )

# app/routers/lantek.py 
@router.post("/import", response_model=BaseResponse[List[LantekScenarioData]])
async def import_lantek_pdf(
    # 다시 project_id에서 scenario_id 로 변경
    scenario_id: int = Form(..., description="데이터를 연결할 시나리오 ID"), 
    file: UploadFile = File(...), 
    db: AsyncSession = Depends(get_db)
):
    # 이미 만들어진 시나리오 ID를 넘겨서 하위에 더미 데이터를 매닮
    await lantek_service.create_dummy_lantek_data(db, scenario_id)
    
    data = await lantek_service.get_lantek_data(db, scenario_id)
    return BaseResponse(
        status=201,
        message="LANTEK 결과 처리에 성공했습니다.",
        data=data
    )

# 3. LANTEK 결과 초기화 (DELETE)
@router.delete("/delete", response_model=BaseResponse)
async def delete_lantek(request: LantekDeleteRequest, db: AsyncSession = Depends(get_db)):
    await lantek_service.delete_lantek_data(db, request.scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오 LANTEK 초기화가 완료되었습니다.",
        data=None
    )