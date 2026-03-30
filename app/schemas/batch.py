# schemas/batch.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class BatchBase(BaseModel):
    scenario_id: int
    batch_order: int

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_order: Optional[int] = None
    completed_at: Optional[datetime] = None

class BatchResponse(BatchBase):
    id: int
    completed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)