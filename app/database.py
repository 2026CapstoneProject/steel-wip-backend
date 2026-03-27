from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

DATABASE_URL = (
    f"mysql+aiomysql://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_async_engine(DATABASE_URL, echo=True)

async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with async_session() as session:
        yield session
