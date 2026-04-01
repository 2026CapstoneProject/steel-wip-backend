# schemas/steel_wip_history.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class SteelWipHistoryBase(BaseModel):
    steel_wip_id: int
    history_location: Optional[int] = None
    history_stack_level: int
    history_stack_height: int
    history_loc_time: Optional[datetime] = None

class SteelWipHistoryCreate(SteelWipHistoryBase):
    pass

class SteelWipHistoryUpdate(BaseModel):
    pass # 이력은 보통 수정하지 않음

class SteelWipHistoryResponse(SteelWipHistoryBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)