# schemas/lazer_cutting.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.schemas.enums import CuttingStatus, CuttingPriority

class LazerCuttingBase(BaseModel):
    scenario_id: Optional[int] = None
    priority: CuttingPriority = CuttingPriority.LOW
    status: CuttingStatus = CuttingStatus.PENDING
    steel_wip_id: Optional[int] = None
    batch_id: Optional[int] = None

class LazerCuttingCreate(LazerCuttingBase):
    estimated_cutting_time: Optional[int] = None

class LazerCuttingUpdate(BaseModel):
    priority: Optional[CuttingPriority] = None
    status: Optional[CuttingStatus] = None
    real_cutting_time: Optional[int] = None
    batch_id: Optional[int] = None

class LazerCuttingResponse(LazerCuttingBase):
    id: int
    estimated_cutting_time: Optional[int] = None
    real_cutting_time: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)