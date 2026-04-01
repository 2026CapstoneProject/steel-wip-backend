# schemas/location.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class LocationBase(BaseModel):
    loc_name: Optional[str] = None
    loc_can_stock: Optional[bool] = None
    loc_stack_height: Optional[int] = None

class LocationCreate(LocationBase):
    pass

class LocationUpdate(BaseModel):
    loc_name: Optional[str] = None
    loc_can_stock: Optional[bool] = None
    loc_stack_height: Optional[int] = None

class LocationResponse(LocationBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)