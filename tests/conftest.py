# tests/conftest.py
"""
공통 테스트 픽스처 모음.

[전략] 실제 MySQL 없이 SQLite 인메모리 DB를 사용한다.
  - 속도가 빠르고 CI 환경에서도 추가 인프라 없이 실행 가능
  - MySQL 전용 server_default(now() 등)는 픽스처에서 값을 직접 지정해 우회
  - Enum, TINYINT 등 MySQL 방언 타입은 SQLAlchemy가 SQLite 호환 타입으로 자동 변환

[환경변수 주입]
  app/core/config.py의 Settings()는 .env 파일을 필수로 읽는다.
  pytest 실행 시 .env가 없으면 ValidationError가 발생하므로,
  앱 모듈을 import하기 전에 테스트용 더미 값을 os.environ에 먼저 주입한다.
  (실제 DB 접속은 하지 않으므로 값 자체는 의미 없음)
"""

import os

# ── 앱 import 전에 반드시 먼저 실행되어야 함 ──────────────────────────
os.environ.setdefault("DB_USER",     "test_user")
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("DB_HOST",     "localhost")
os.environ.setdefault("DB_PORT",     "3306")
os.environ.setdefault("DB_NAME",     "test_db")
# ──────────────────────────────────────────────────────────────────────

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

# ── MySQL 방언 타입 → SQLite 호환 패치 ───────────────────────────────────
# SQLAlchemy의 MySQL 전용 TINYINT를 SQLite 컴파일러가 이해하지 못하는 문제를 해결.
# 모델 코드(production)는 건드리지 않고, 테스트 환경에서만 동적으로 패치한다.
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.dialects.mysql import TINYINT as MySQLTINYINT  # noqa: F401
SQLiteTypeCompiler.visit_TINYINT = lambda self, type_, **kw: "INTEGER"
# ──────────────────────────────────────────────────────────────────────

from app.models import Base
from app.database import get_db
from main import app
from tests.load_fixtures import load_dump_fixtures

# ── SQLite 인메모리 DB URL ─────────────────────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ── DB 세션 픽스처 ────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """
    테스트마다 독립된 SQLite 인메모리 DB를 생성하고 종료 시 삭제한다.
    StaticPool을 사용해 동일 커넥션을 공유(인메모리 DB는 커넥션별로 분리되기 때문).
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ── dump 데이터 포함 DB 세션 픽스처 ──────────────────────────────────
@pytest_asyncio.fixture
async def db_with_dump() -> AsyncSession:
    """
    SQLite 인메모리 DB를 생성하고, capstoneDB-backup의 dump 데이터를 로드한다.
    통합 테스트(dump 기반 실 데이터 검증)에서 사용한다.

    주의:
      - dump 파일의 일부 행(NULL in NOT NULL 컬럼 등)은 자동으로 건너뜀
      - scenarios 테이블의 status=NULL인 행(id=2)은 로드되지 않음
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with session_factory() as session:
        await load_dump_fixtures(session)
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ── HTTP 클라이언트 픽스처 ─────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """
    FastAPI 앱을 실제로 띄우지 않고 ASGI Transport로 테스트하는 AsyncClient.
    get_db 의존성을 테스트용 db_session으로 교체한다.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_with_dump(db_with_dump: AsyncSession) -> AsyncClient:
    """
    dump 데이터가 로드된 DB를 사용하는 HTTP 클라이언트 픽스처.
    통합 테스트에서 사용한다.
    """
    async def override_get_db():
        yield db_with_dump

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
