# app/schemas/lantek.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class LantekInput(BaseModel):
    manufacturer: str
    material: str
    thickness: float
    width: float
    height: float  # DB의 length와 매핑
    materialType: str  # "원자재" | "재공품"


class LantekEstimatedWip(BaseModel):
    id: int
    qrCode: Optional[str] = None       # PDF의 QR코드 컬럼값
    jobName: Optional[str] = None
    thickness: float
    width: float
    height: float
    weight: Optional[float] = None
    memo: Optional[str] = None


class LantekCutting(BaseModel):
    id: int
    jobName: Optional[str] = None
    ncCode: Optional[str] = None       # CNC 프로그램 번호
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