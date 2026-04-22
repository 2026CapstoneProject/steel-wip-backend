# app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.crud import user as user_crud
from app.schemas import UserCreate, UserUpdate, UserResponse, BaseResponse

router = APIRouter()

@router.post("", response_model=BaseResponse[UserResponse])
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    # 유저명 중복 체크
    existing_user = await user_crud.get_user_by_username(db, user.username)
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 유저명입니다.")
        
    new_user = await user_crud.create_user(db, user)
    return BaseResponse(status=201, message="사용자가 성공적으로 생성되었습니다.", data=new_user)

@router.get("/{user_id}", response_model=BaseResponse[UserResponse])
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await user_crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return BaseResponse(status=200, message="사용자 조회 성공", data=user)

@router.get("", response_model=BaseResponse[List[UserResponse]])
async def list_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    users = await user_crud.get_users(db, skip, limit)
    return BaseResponse(status=200, message="사용자 목록 조회 성공", data=users)