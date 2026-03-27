from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app import models, schemas  # 경로 수정 주의

# 1. 사용자 생성 (POST)
async def create(db: AsyncSession, user: schemas.UserCreate):
    db_user = models.User(**user.model_dump())
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

# 2. 모든 사용자 조회 (GET)
async def get_all(db: AsyncSession):
    result = await db.execute(select(models.User))
    return result.scalars().all()

# 3. 특정 사용자 조회 (GET)
async def get(db: AsyncSession, user_id: int):
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    return result.scalars().first()

# 4. 사용자 정보 수정 (PATCH)
async def update(db: AsyncSession, user_id: int, user_update: schemas.UserUpdate):
    db_user = await get(db, user_id)
    if not db_user:
        return None
    
    update_data = user_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_user, key, value)
        
    await db.commit()
    await db.refresh(db_user)
    return db_user

# 5. 사용자 삭제 (DELETE)
async def delete(db: AsyncSession, user_id: int):
    db_user = await get(db, user_id)
    if not db_user:
        return None
    
    await db.delete(db_user)
    await db.commit()
    return db_user
