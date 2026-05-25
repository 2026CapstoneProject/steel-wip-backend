# app/schemas/scenario.py
from pydantic import BaseModel, ConfigDict
from pydantic import Field
from typing import List,Optional
# datetime과 함께 date도 임포트
from datetime import datetime, date 
from app.schemas.enums import ScenarioStatus, LazerType, CuttingPriority

class ScenarioBase(BaseModel):
    title: str
    scenario_order: int = 0
    status: Optional[ScenarioStatus] = None
    scenario_due: date  # datetime -> date 수정
    lazer_name: Optional[LazerType] = None
    process_priority: Optional[CuttingPriority] = CuttingPriority.LOW
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
    lazer_name: Optional[LazerType] = LazerType.LAZER1
    process_priority: Optional[CuttingPriority] = CuttingPriority.LOW



class BatchItemDetail(BaseModel):
    batchItemId: int 
    batchItemAction: str
    steelWipId: int
    qrCode: Optional[str] = None
    ncCode: Optional[str] = None  
    manufacturer: str
    material: str
    thickness: float
    width: float
    length: float
    weight: float
    fromLocation: Optional[str] = None
    toLocation: Optional[str] = None
    expectedStartTime: int
    expectedRunningTime: Optional[int] = None  

class ScenarioSolverSummary(BaseModel):
    status: str
    objective: int
    mipGap: float
    solutions: int
    solveSeconds: float
    makespanMinutes: float

class ScenarioJobScheduleItem(BaseModel):
    jobName: str
    sequence: int
    startMinute: float
    endMinute: float
    pickWips: List[int]
    outputWips: List[int]

class ScenarioCraneScheduleItem(BaseModel):
    order: int
    batchItemId: Optional[int] = None
    batchId: Optional[int] = None
    batchItemOrder: Optional[int] = None
    action: str
    actionLabel: Optional[str] = None
    steelWipId: int
    qrCode: Optional[str] = None
    ncCode: Optional[str] = None
    thickness: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    fromLocation: str
    toLocation: str
    eventMinute: float
    expectedStartMinute: Optional[float] = None
    expectedDurationMinutes: Optional[float] = None
    expectedEndMinute: Optional[float] = None
    moveType: Optional[str] = None

class ScenarioResultData(BaseModel):
    projectId: int
    projectTitle: str
    projectDue: Optional[date] = None
    scenarioId: int
    scenarioTitle: str
    scenarioDue: date
    orderedAt: Optional[datetime] = None
    numInputWip: int = 0
    emergencyOrNot: bool = False
    lazerName: str
    status: Optional[str] = None
    totalCuttingTime: int
    totalWipNum: int
    totalCraneMove: int
    totalMoveNum: int
    batchItems: List[BatchItemDetail]
    solverSummary: Optional[ScenarioSolverSummary] = None
    jobSchedule: List[ScenarioJobScheduleItem] = Field(default_factory=list)
    craneSchedule: List[ScenarioCraneScheduleItem] = Field(default_factory=list)

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
    numInputWip: int
    status: Optional[str]
    
class SentProjectHistory(BaseModel):
    projectId: int
    projectTitle: str
    projectDue: date
    scenarios: List[SentScenarioItem]

class NcCodeUpdateRequest(BaseModel):
    batchItemId: int
    ncCode: str
