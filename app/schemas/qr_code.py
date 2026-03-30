# schemas/qr_code.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class QrCodeBase(BaseModel):
    qr_code: Optional[str] = None
    steel_wip_id: Optional[int] = None

class QrCodeCreate(QrCodeBase):
    pass

class QrCodeUpdate(BaseModel):
    steel_wip_id: Optional[int] = None

class QrCodeResponse(QrCodeBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)