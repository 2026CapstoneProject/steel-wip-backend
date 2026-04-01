from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import users, wips

app = FastAPI(title="철강 잔재 재고관리 API", version="1.0.0")

# ---------------------------------------------------------
# 1. 공통 에러 응답 포맷 핸들러
# ---------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": exc.status_code,
            "message": str(exc.detail),
            "data": None
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_messages = [f"{err['loc'][-1]}: {err['msg']}" for err in exc.errors()]
    combined_message = "데이터 유효성 검사 실패 - " + ", ".join(error_messages)

    return JSONResponse(
        status_code=422,
        content={
            "status": 422,
            "message": combined_message,
            "data": None
        }
    )

# ---------------------------------------------------------
# 2. 라우터 등록
# ---------------------------------------------------------

app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(wips.router, prefix="/api/steelWip", tags=["Steel WIPs"])

@app.get("/")
async def root():
    return {
        "message": "서버가 정상 구동 중입니다. /docs 에 접속하여 API를 테스트하세요."
    }
