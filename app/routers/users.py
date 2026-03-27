from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from .. import schemas
from .. import crud
from ..database import get_db

router = APIRouter(prefix="/users", tags=["Users"])

# 1. 사용자 생성
@router.post("/", response_model=schemas.BaseResponse[schemas.UserResponse], status_code=status.HTTP_201_CREATED)
async def create_user(user: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    db_user = await crud.user.create(db=db, user=user)
    return schemas.BaseResponse(
        status=201,
        message="사용자 생성에 성공했습니다.",
        data=db_user
    )

# 2. 모든 사용자 조회 (리스트 반환)
@router.get("/", response_model=schemas.BaseResponse[List[schemas.UserResponse]])
async def read_users(db: AsyncSession = Depends(get_db)):
    users = await crud.user.get_all(db=db)
    return schemas.BaseResponse(
        status=200,
        message="사용자 목록 조회에 성공했습니다.",
        data=users
    )

# 3. 특정 사용자 조회
@router.get("/{user_id}", response_model=schemas.BaseResponse[schemas.UserResponse])
async def read_user(user_id: int, db: AsyncSession = Depends(get_db)):
    db_user = await crud.user.get(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    
    return schemas.BaseResponse(
        status=200,
        message="사용자 상세 조회에 성공했습니다.",
        data=db_user
    )

# 4. 사용자 정보 수정
@router.patch("/{user_id}", response_model=schemas.BaseResponse[schemas.UserResponse])
async def update_user(user_id: int, user_update: schemas.UserUpdate, db: AsyncSession = Depends(get_db)):
    db_user = await crud.user.update(db, user_id=user_id, user_update=user_update)
    if db_user is None:
        raise HTTPException(status_code=404, detail="수정할 사용자를 찾을 수 없습니다.")
    
    return schemas.BaseResponse(
        status=200,
        message="사용자 정보 수정에 성공했습니다.",
        data=db_user
    )

# 5. 사용자 삭제 (삭제 시에는 data 부분 생략 가능)
@router.delete("/{user_id}", response_model=schemas.BaseResponse[None])
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    db_user = await crud.user.delete(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="삭제할 사용자를 찾을 수 없습니다.")
    
    return schemas.BaseResponse(
        status=200,
        message="사용자 삭제에 성공했습니다.",
        data=None
    )
