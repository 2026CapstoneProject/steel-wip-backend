# app/routers/auth.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
import bcrypt

from jose import jwt

from app.database import async_session
from app.models import Users

router = APIRouter()

# ─── 설정값 ───────────────────────────────────────────────
from app.core.config import settings

SECRET_KEY = settings.JWT_SECRET_KEY
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.JWT_EXPIRE_MINUTES

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

# ─── DB 의존성 ────────────────────────────────────────────
async def get_db():
    async with async_session() as session:
        yield session

# ─── 스키마 ───────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    accessToken: str
    user: dict

# ─── JWT 생성 ─────────────────────────────────────────────
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ─── POST /api/auth/login ─────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    # 1. username으로 유저 조회
    result = await db.execute(select(Users).where(Users.username == body.username))
    user = result.scalar_one_or_none()

    # 2. 유저 없거나 비밀번호 불일치
    if not user or not verify_password(body.password, user.password):
        raise HTTPException(
            status_code=401,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )

    # 3. last_login_at 업데이트
    await db.execute(
        update(Users).where(Users.id == user.id).values(last_login_at=datetime.utcnow())
    )
    await db.commit()

    # 4. JWT 발급
    token = create_access_token({
        "sub": str(user.id),
        "username": user.username,
        "role": user.role.value
    })

    return LoginResponse(
        accessToken=token,
        user={
            "id": user.id,
            "username": user.username,
            "role": user.role.value
        }
    )

# ─── POST /api/auth/logout ────────────────────────────────
@router.post("/logout")
async def logout():
    # JWT는 stateless이므로 프론트에서 토큰을 삭제하는 것으로 처리
    # 추후 Redis 블랙리스트 방식으로 확장 가능
    return {"message": "로그아웃 되었습니다."}