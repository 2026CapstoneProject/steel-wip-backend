# app/schemas/scenario.py
from pydantic import BaseModel, ConfigDict
from pydantic import Field
from typing import List,Optional
# datetime과 함께 date도 임포트
from datetime import datetime, date 
from app.schemas.enums import ScenarioStatus, LazerType

class ScenarioBase(BaseModel):
    title: str
    scenario_order: int = 0
    status: Optional[ScenarioStatus] = None
    scenario_due: date  # datetime -> date 수정
    lazer_name: Optional[LazerType] = None
    project_id: Optional[int] = None
    creator_id: Optional[int] = None
    assignee_id: Optional[int] = None

class ScenarioCreate(ScenarioBase):
    pass

class ScenarioUpdate(BaseModel):
    status: Optional[ScenarioStatus] = None
    scenario_order: Optional[int] = None
    completed_at: Optional[datetime] = None

class ScenarioResponse(ScenarioBase):
    id: int
    created_at: datetime
    ordered_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class ScenarioCreateRequest(BaseModel):
    project_id: int
    scenario_due: date
    # 필요하다면 lazer_name 등도 받을 수 있지만 일단 필수값만 정의
    lazer_name: Optional[str] = "LAZER1"



class BatchItemDetail(BaseModel):
    batchItemAction: str
    steelWipId: int
    manufacturer: str
    material: str
    thickness: float
    width: float
    length: float
    weight: float
    fromLocation: Optional[str] = None
    toLocation: Optional[str] = None
    expectedStartTime: int

class ScenarioResultData(BaseModel):
    projectId: int
    projectTitle: str
    scenarioId: int
    scenarioTitle: str
    scenarioDue: date
    lazerName: str
    totalCuttingTime: int
    totalWipNum: int
    totalCraneMove: int
    totalMoveNum: int
    batchItems: List[BatchItemDetail]

# app/schemas/scenario.py (기존 내용 하단에 추가)

class ScenarioHistoryItem(BaseModel):
    id: int
    title: str
    due: date
    lazerName: str
    selectedWips: int
    # 파이썬 변수명에는 #을 쓸 수 없으므로 alias(별칭)를 사용하여 JSON 키를 강제 지정합니다.
    num_relocation: int = Field(alias="#relocation")
    num_crane: int = Field(alias="#crane")
    totalMinute: int

    model_config = ConfigDict(populate_by_name=True)

class ProjectScenarioHistory(BaseModel):
    projectId: int
    projectTitle: str
    scenario: List[ScenarioHistoryItem]

# app/schemas/scenario.py

class SentScenarioItem(BaseModel):
    scenarioId: int
    scenarioTitle: str
    scenarioDue: date
    orderedAt: datetime
    numInputWip: int  # 생산 계획으로 세운 투입 WIPs 개수 (PICKING 배치 아이템 수)

class SentProjectHistory(BaseModel):
    projectId: int
    projectTitle: str
    projectDue: date
    scenarios: List[SentScenarioItem]