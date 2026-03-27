from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.models import UserRole


class UserBase(BaseModel):
    username: str
    department: Optional[str] = None
    role: UserRole
    user_num: int


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    username: Optional[str] = None
    department: Optional[str] = None
    role: Optional[UserRole] = None
    user_num: Optional[int] = None


class UserResponse(UserBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
