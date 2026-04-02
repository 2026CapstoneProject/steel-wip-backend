# app/schemas/scenario.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
# datetime과 함께 date도 임포트
from datetime import datetime, date 
from app.schemas.enums import ScenarioStatus, LazerType

class ScenarioBase(BaseModel):
    title: str
    scenario_order: int = 0
    status: ScenarioStatus = ScenarioStatus.DRAFT
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