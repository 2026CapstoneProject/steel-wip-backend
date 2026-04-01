# app/crud/user.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.models import Users
from app.schemas.user import UserCreate, UserUpdate

async def create_user(db: AsyncSession, user_data: UserCreate) -> Users:
    """새로운 사용자 생성"""
    db_user = Users(**user_data.model_dump())
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[Users]:
    """ID로 특정 사용자 단일 조회"""
    result = await db.execute(select(Users).filter(Users.id == user_id))
    return result.scalars().first()

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[Users]:
    """유저명으로 사용자 단일 조회"""
    result = await db.execute(select(Users).filter(Users.username == username))
    return result.scalars().first()

async def get_users(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Users]:
    """사용자 목록 조회 (페이지네이션)"""
    result = await db.execute(select(Users).offset(skip).limit(limit))
    return list(result.scalars().all())

async def update_user(db: AsyncSession, user_id: int, user_data: UserUpdate) -> Optional[Users]:
    """사용자 정보 수정"""
    db_user = await get_user_by_id(db, user_id)
    if not db_user:
        return None
        
    update_data = user_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_user, key, value)
        
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def delete_user(db: AsyncSession, user_id: int) -> bool:
    """사용자 삭제"""
    db_user = await get_user_by_id(db, user_id)
    if not db_user:
        return False
        
    await db.delete(db_user)
    await db.commit()
    return True