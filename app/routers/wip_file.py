from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from app.database import get_db
from app.models import SteelWip, SteelWipStatus
from app.services.wip_file_service import preview_wip_file, confirm_wip_updates, delete_wip_with_reorder

router = APIRouter()

ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


# ── Pydantic 스키마 ──────────────────────────────────────────────────────────

class WipUpdateItem(BaseModel):
    wip_id: int
    after: dict


class WipCreateItem(BaseModel):
    qr_code: str
    qr_id: int | None = None
    fields: dict


class ConfirmRequest(BaseModel):
    updates: List[WipUpdateItem] = []
    creates: List[WipCreateItem] = []


# ── 1단계: 미리보기 ──────────────────────────────────────────────────────────

@router.post("/file/preview")
async def preview_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="파일 크기가 10MB를 초과합니다.")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        result = await preview_wip_file(db=db, filename=filename, content=content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 처리 중 오류가 발생했습니다: {str(e)}")

    return {
        "status": 200,
        "message": "미리보기 완료. 확인 후 /file/confirm 으로 확정하세요.",
        "data": {
            "to_update_count":       len(result["to_update"]),
            "to_create_count":       len(result["to_create"]),   # ← 추가
            "unchanged_count":       result["unchanged"],
            "skipped_count":         len(result["skipped"]),
            "missing_in_file_count": len(result["missing_in_file"]),
            "to_update":             result["to_update"],
            "to_create":             result["to_create"],         # ← 추가
            "skipped":               result["skipped"],
            "missing_in_file":       result["missing_in_file"],
        },
    }


# ── 2단계: 확정 반영 ─────────────────────────────────────────────────────────

@router.post("/file/confirm")
async def confirm_upload(
    body: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.updates and not body.creates:
        raise HTTPException(status_code=400, detail="확정할 항목이 없습니다.")

    try:
        result = await confirm_wip_updates(
            db=db,
            updates=[item.model_dump() for item in body.updates],
            creates=[item.model_dump() for item in body.creates],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"확정 처리 중 오류가 발생했습니다: {str(e)}")

    return {
        "status": 200,
        "message": "확정 반영 완료",
        "data": {
            "updated_count":    len(result["updated"]),
            "created_count":    len(result["created"]),
            "skipped_count":    len(result["skipped"]),
            "updated_wip_ids":  result["updated"],
            "created_qr_codes": result["created"],
            "skipped":          result["skipped"],
        },
    }


# ── 삭제 ─────────────────────────────────────────────────────────────────────

@router.delete("/file")
async def delete_wips_by_ids(
    wip_ids: List[int],
    db: AsyncSession = Depends(get_db),
):
    if not wip_ids:
        raise HTTPException(status_code=400, detail="삭제할 wip_id 목록이 비어 있습니다.")

    try:
        result = await delete_wip_with_reorder(db=db, wip_ids=wip_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"삭제 처리 중 오류가 발생했습니다: {str(e)}")

    return {
        "status": 200,
        "message": "삭제 처리 완료",
        "data": {
            "deleted_count":   len(result["deleted"]),
            "skipped_count":   len(result["skipped"]),
            "deleted_wip_ids": result["deleted"],
            "skipped":         result["skipped"],
        },
    }