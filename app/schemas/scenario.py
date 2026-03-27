from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from app.models import ScenarioStatus, LazerType


class ScenarioCreate(BaseModel):
    title: str
    creator_id: int
    lazer_name: Optional[LazerType] = None
    ordered_at: Optional[datetime] = None


class ScenarioUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[ScenarioStatus] = None
    assignee_id: Optional[int] = None
    lazer_name: Optional[LazerType] = None
    ordered_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ScenarioResponse(BaseModel):
    id: int
    title: str
    status: ScenarioStatus
    creator_id: int
    assignee_id: Optional[int] = None
    lazer_name: Optional[LazerType] = None
    created_at: datetime
    ordered_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
