from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app import schemas, crud
from app.database import get_db

router = APIRouter(prefix="/scenarios", tags=["Scenarios"])


# 1. 시나리오 생성
@router.post("/", response_model=schemas.BaseResponse[schemas.ScenarioResponse], status_code=status.HTTP_201_CREATED)
async def create_scenario(scenario: schemas.ScenarioCreate, db: AsyncSession = Depends(get_db)):
    db_scenario = await crud.scenario.create(db=db, scenario=scenario)
    return schemas.BaseResponse(status=201, message="시나리오 생성에 성공했습니다.", data=db_scenario)


# 2. 전체 시나리오 조회
@router.get("/", response_model=schemas.BaseResponse[List[schemas.ScenarioResponse]])
async def read_scenarios(db: AsyncSession = Depends(get_db)):
    scenarios = await crud.scenario.get_all(db=db)
    return schemas.BaseResponse(status=200, message="시나리오 목록 조회에 성공했습니다.", data=scenarios)


# 3. 특정 시나리오 조회
@router.get("/{scenario_id}", response_model=schemas.BaseResponse[schemas.ScenarioResponse])
async def read_scenario(scenario_id: int, db: AsyncSession = Depends(get_db)):
    db_scenario = await crud.scenario.get(db, scenario_id=scenario_id)
    if db_scenario is None:
        raise HTTPException(status_code=404, detail="시나리오를 찾을 수 없습니다.")
    return schemas.BaseResponse(status=200, message="시나리오 조회에 성공했습니다.", data=db_scenario)


# 4. 시나리오 상태 수정
@router.patch("/{scenario_id}", response_model=schemas.BaseResponse[schemas.ScenarioResponse])
async def update_scenario(scenario_id: int, scenario_update: schemas.ScenarioUpdate, db: AsyncSession = Depends(get_db)):
    db_scenario = await crud.scenario.update(db, scenario_id=scenario_id, scenario_update=scenario_update)
    if db_scenario is None:
        raise HTTPException(status_code=404, detail="수정할 시나리오를 찾을 수 없습니다.")
    return schemas.BaseResponse(status=200, message="시나리오 수정에 성공했습니다.", data=db_scenario)
