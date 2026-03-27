from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app import models, schemas


async def create(db: AsyncSession, scenario: schemas.ScenarioCreate):
    db_scenario = models.Scenario(**scenario.model_dump())
    db.add(db_scenario)
    await db.commit()
    await db.refresh(db_scenario)
    return db_scenario


async def get_all(db: AsyncSession):
    result = await db.execute(select(models.Scenario))
    return result.scalars().all()


async def get(db: AsyncSession, scenario_id: int):
    result = await db.execute(
        select(models.Scenario).where(models.Scenario.id == scenario_id)
    )
    return result.scalars().first()


async def update(db: AsyncSession, scenario_id: int, scenario_update: schemas.ScenarioUpdate):
    db_scenario = await get(db, scenario_id)
    if not db_scenario:
        return None

    update_data = scenario_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_scenario, key, value)

    await db.commit()
    await db.refresh(db_scenario)
    return db_scenario
