# schemas/scenario.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from app.schemas.enums import ScenarioStatus, LazerType

class ScenarioBase(BaseModel):
    title: str
    scenario_order: int = 0
    status: ScenarioStatus = ScenarioStatus.DRAFT
    scenario_due: datetime
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