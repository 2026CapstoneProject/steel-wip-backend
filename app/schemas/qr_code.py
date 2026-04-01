# schemas/qr_code.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class QrCodeBase(BaseModel):
    qr_code: Optional[str] = None

class QrCodeCreate(QrCodeBase):
    pass

class QrCodeUpdate(BaseModel):
    pass

class QrCodeResponse(QrCodeBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)
    