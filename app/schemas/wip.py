# schemas/wip.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.schemas.enums import WipStatus

class SteelWipBase(BaseModel):
    status: WipStatus = WipStatus.REGISTERED
    manufacturer: str
    material: str
    thickness: float
    width: float
    length: float
    weight: float
    location_id: Optional[int] = None
    stack_level: Optional[int] = None
    qr_id: Optional[int] = None

class SteelWipCreate(SteelWipBase):
    pass

class SteelWipUpdate(BaseModel):
    status: Optional[WipStatus] = None
    location_id: Optional[int] = None
    stack_level: Optional[int] = None

class SteelWipResponse(SteelWipBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)