# app/schemas/estimated_wip.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class EstimatedWipBase(BaseModel):
    lazer_cutting_id: Optional[int] = None
    # 컬럼명 통일
    manufacturer: Optional[str] = None
    material: Optional[str] = None
    thickness: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    weight: Optional[float] = None
    qr_id: Optional[int] = None

class EstimatedWipCreate(EstimatedWipBase):
    pass

class EstimatedWipUpdate(BaseModel):
    # 수정 가능한 컬럼들
    weight: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None

class EstimatedWipResponse(EstimatedWipBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)