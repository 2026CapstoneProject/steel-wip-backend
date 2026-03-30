# schemas/estimated_wip.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class EstimatedWipBase(BaseModel):
    lazer_cutting_id: Optional[int] = None
    estimated_wip_thickness: Optional[float] = None
    estimated_wip_width: Optional[float] = None
    estimated_wip_length: Optional[float] = None
    estimated_wip_weight: Optional[float] = None
    qr_id: Optional[int] = None

class EstimatedWipCreate(EstimatedWipBase):
    pass

class EstimatedWipUpdate(BaseModel):
    estimated_wip_weight: Optional[float] = None

class EstimatedWipResponse(EstimatedWipBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)