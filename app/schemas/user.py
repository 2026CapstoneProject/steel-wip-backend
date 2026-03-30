# schemas/user.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from schemas.enums import UserRole

class UserBase(BaseModel):
    username: str
    department: str
    role: UserRole
    user_num: int

class UserCreate(UserBase):
    pass

class UserUpdate(BaseModel):
    username: Optional[str] = None
    department: Optional[str] = None
    role: Optional[UserRole] = None

class UserResponse(UserBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)