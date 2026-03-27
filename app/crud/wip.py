from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app import models, schemas


async def create(db: AsyncSession, wip: schemas.SteelWipCreate):
    db_wip = models.SteelWip(**wip.model_dump())
    db.add(db_wip)
    await db.commit()
    await db.refresh(db_wip)
    return db_wip


async def get_all(db: AsyncSession):
    result = await db.execute(select(models.SteelWip))
    return result.scalars().all()


async def get(db: AsyncSession, wip_id: int):
    result = await db.execute(
        select(models.SteelWip).where(models.SteelWip.id == wip_id)
    )
    return result.scalars().first()


async def update(db: AsyncSession, wip_id: int, wip_update: schemas.SteelWipUpdate):
    db_wip = await get(db, wip_id)
    if not db_wip:
        return None

    update_data = wip_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_wip, key, value)

    await db.commit()
    await db.refresh(db_wip)
    return db_wip


async def delete(db: AsyncSession, wip_id: int):
    db_wip = await get(db, wip_id)
    if not db_wip:
        return None

    await db.delete(db_wip)
    await db.commit()
    return db_wip
