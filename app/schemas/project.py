# schemas/project.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class ProjectBase(BaseModel):
    title: str
    project_due: datetime
    emergency_or_not: bool = False

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    project_due: Optional[datetime] = None
    emergency_or_not: Optional[bool] = None

class ProjectResponse(ProjectBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)