# app/schemas/project.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
# datetime 대신 date 임포트
from datetime import date 

class ProjectBase(BaseModel):
    title: str
    project_due: date  # datetime -> date 수정

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    project_due: Optional[date] = None  # datetime -> date 수정

class ProjectResponse(ProjectBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)

class ProjectSearchData(BaseModel):
    search: Optional[str] = None
    projects: List[ProjectResponse]