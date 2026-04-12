# tests/test_users.py
"""
사용자 API 테스트
  - POST /api/users            (사용자 생성)
  - GET  /api/users/{user_id}  (단건 조회)
  - GET  /api/users            (목록 조회)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Users


# ══════════════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════════════

async def make_user(
    db: AsyncSession,
    username: str = "홍길동",
    department: str = "생산부",
    role: str = "FIELD",
    user_num: int = 1001,
) -> Users:
    user = Users(username=username, department=department, role=role, user_num=user_num)
    db.add(user)
    await db.flush()
    return user


# ══════════════════════════════════════════════════════════════════════
# POST /api/users — 사용자 생성
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_user_success(client: AsyncClient, db_session: AsyncSession):
    """사용자 생성 성공 — 201 + data 반환"""
    payload = {
        "username": "김현장",
        "department": "생산부",
        "role": "FIELD",
        "user_num": 1001,
    }
    response = await client.post("/api/users", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 201
    assert body["message"] == "사용자가 성공적으로 생성되었습니다."
    assert body["data"]["username"] == "김현장"
    assert body["data"]["role"] == "FIELD"
    assert body["data"]["department"] == "생산부"


@pytest.mark.asyncio
async def test_create_user_office_role(client: AsyncClient, db_session: AsyncSession):
    """OFFICE 역할 사용자 생성"""
    payload = {
        "username": "이계획",
        "department": "계획부",
        "role": "OFFICE",
        "user_num": 2001,
    }
    response = await client.post("/api/users", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["role"] == "OFFICE"


@pytest.mark.asyncio
async def test_create_user_duplicate_username(client: AsyncClient, db_session: AsyncSession):
    """중복 username 사용 시 400"""
    await make_user(db_session, username="중복유저")
    await db_session.commit()

    payload = {
        "username": "중복유저",
        "department": "생산부",
        "role": "FIELD",
        "user_num": 9999,
    }
    response = await client.post("/api/users", json=payload)

    assert response.status_code == 400
    assert "이미 존재하는 유저명" in response.json()["message"]


@pytest.mark.asyncio
async def test_create_user_invalid_role(client: AsyncClient, db_session: AsyncSession):
    """유효하지 않은 role → 422"""
    payload = {
        "username": "테스트",
        "department": "테스트부",
        "role": "INVALID_ROLE",
        "user_num": 0,
    }
    response = await client.post("/api/users", json=payload)

    assert response.status_code == 422


# ══════════════════════════════════════════════════════════════════════
# GET /api/users/{user_id} — 단건 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_user_success(client: AsyncClient, db_session: AsyncSession):
    """존재하는 사용자 단건 조회"""
    user = await make_user(db_session, username="박팀장", department="현장팀", role="FIELD", user_num=3001)
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == user.id
    assert data["username"] == "박팀장"
    assert data["department"] == "현장팀"
    assert data["user_num"] == 3001


@pytest.mark.asyncio
async def test_get_user_not_found(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 사용자 조회 → 404"""
    response = await client.get("/api/users/99999")

    assert response.status_code == 404
    assert "찾을 수 없습니다" in response.json()["message"]


# ══════════════════════════════════════════════════════════════════════
# GET /api/users — 목록 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_users_empty(client: AsyncClient, db_session: AsyncSession):
    """사용자가 없을 때 빈 배열 반환"""
    response = await client.get("/api/users")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["data"] == []


@pytest.mark.asyncio
async def test_list_users_multiple(client: AsyncClient, db_session: AsyncSession):
    """여러 사용자 목록 조회"""
    await make_user(db_session, username="유저A", user_num=1)
    await make_user(db_session, username="유저B", user_num=2)
    await make_user(db_session, username="유저C", user_num=3)
    await db_session.commit()

    response = await client.get("/api/users")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 3
    usernames = {d["username"] for d in data}
    assert "유저A" in usernames
    assert "유저B" in usernames
    assert "유저C" in usernames


@pytest.mark.asyncio
async def test_list_users_skip_limit(client: AsyncClient, db_session: AsyncSession):
    """skip / limit 페이지네이션"""
    for i in range(5):
        await make_user(db_session, username=f"유저{i}", user_num=i)
    await db_session.commit()

    # limit=2로 조회
    response = await client.get("/api/users", params={"skip": 0, "limit": 2})

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2

    # skip=3으로 조회 (5명 중 뒤 2명)
    response2 = await client.get("/api/users", params={"skip": 3, "limit": 10})
    assert len(response2.json()["data"]) == 2
