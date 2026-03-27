from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.models import WipStatus


class SteelWipBase(BaseModel):
    manufacturer: Optional[str] = None
    material: Optional[str] = None
    thickness: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    weight: Optional[float] = None
    location_id: Optional[int] = None
    stack_level: Optional[int] = None


class SteelWipCreate(SteelWipBase):
    pass


class SteelWipUpdate(BaseModel):
    manufacturer: Optional[str] = None
    material: Optional[str] = None
    thickness: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    weight: Optional[float] = None
    location_id: Optional[int] = None
    stack_level: Optional[int] = None
    status: Optional[WipStatus] = None


class SteelWipResponse(SteelWipBase):
    id: int
    status: WipStatus

    model_config = ConfigDict(from_attributes=True)
