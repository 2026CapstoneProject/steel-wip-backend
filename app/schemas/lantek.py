# app/schemas/lantek.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date

class LantekInput(BaseModel):
    manufacturer: str
    material: str
    thickness: float
    width: float
    height: float  # 프론트엔드 명세의 height는 DB의 length와 매핑됩니다.

class LantekEstimatedWip(BaseModel):
    id: int
    plannedWipId: Optional[int] = None
    jobName: Optional[str] = None
    thickness: float
    width: float
    height: float
    weight: Optional[float] = None   # estimated_wips.weight (절단 후 무게 kg)
    memo: Optional[str] = None

class LantekCutting(BaseModel):
    id: int
    jobName: Optional[str] = None
    plannedSourceWipId: Optional[int] = None
    estimatedCuttingTime: str
    input: LantekInput
    estimatedWips: List[LantekEstimatedWip]

class LantekScenarioData(BaseModel):
    projectId: int
    projectTitle: str
    projectDue: date
    scenarioId: int
    scenarioTitle: str
    scenarioDue: date
    lazerName: str
    emergencyOrNot: bool
    lazerCutting: List[LantekCutting]

class LantekDeleteRequest(BaseModel):
    scenario_id: int
