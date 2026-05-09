from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Cookie, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
import bcrypt
from jose import jwt, JWTError

from app.database import async_session
from app.models import Users
from app.core.config import settings

router = APIRouter()

SECRET_KEY = settings.JWT_SECRET_KEY
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.JWT_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = 7

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

async def get_db():
    async with async_session() as session:
        yield session

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    accessToken: str
    user: dict

def create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + expires_delta})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ─── POST /api/auth/login ─────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Users).where(Users.username == body.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    await db.execute(
        update(Users).where(Users.id == user.id).values(last_login_at=datetime.utcnow())
    )
    await db.commit()

    # Access Token (단기)
    access_token = create_token(
        {"sub": str(user.id), "username": user.username, "role": user.role.value},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    # Refresh Token (장기) → HttpOnly Cookie로 전달
    refresh_token = create_token(
        {"sub": str(user.id)},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,        # JS에서 접근 불가 (XSS 방어)
        samesite="lax",       # CSRF 방어
        max_age=60 * 60 * 24 * REFRESH_TOKEN_EXPIRE_DAYS,
        # secure=True,        # HTTPS 환경에서는 이 줄 활성화
    )

    return LoginResponse(
        accessToken=access_token,
        user={"id": user.id, "username": user.username, "role": user.role.value}
    )

# ─── POST /api/auth/refresh ───────────────────────────────
@router.post("/refresh")
async def refresh(refresh_token: Optional[str] = Cookie(default=None)):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token이 없습니다.")
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    except JWTError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었거나 유효하지 않습니다.")

    new_access_token = create_token(
        {"sub": user_id},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"accessToken": new_access_token}

# ─── POST /api/auth/logout ────────────────────────────────
@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("refresh_token")  # Cookie 삭제
    return {"message": "로그아웃 되었습니다."}