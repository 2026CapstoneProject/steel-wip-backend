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

class SteelWipWithQrResponse(SteelWipResponse):
    qr_code_value: Optional[str] = None    # qr_codes 테이블에서 조인해 가져올 값
    location_name: Optional[str] = None   # locations 테이블에서 조인해 가져올 위치명