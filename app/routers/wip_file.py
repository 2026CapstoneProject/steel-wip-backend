# app/routers/wip_file.py
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from app.database import get_db
from app.models import SteelWip, SteelWipStatus
from app.services.wip_file_service import preview_wip_file, confirm_wip_updates

router = APIRouter()

ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


# ── Pydantic 스키마 ──────────────────────────────────────────────────────────

class WipUpdateItem(BaseModel):
    wip_id: int
    after: dict  # _extract_fields 반환값과 동일한 구조


class ConfirmRequest(BaseModel):
    updates: List[WipUpdateItem]  # 사용자가 선택한 항목만

class WipCreateItem(BaseModel):
    qr_code: str
    qr_id: int | None = None   # QR이 이미 DB에 있으면 전달, 없으면 None
    fields: dict


class ConfirmRequest(BaseModel):
    updates: List[WipUpdateItem]  # 수정할 항목
    creates: List[WipCreateItem]  # 신규 등록할 항목  ← 추가



# ── 1단계: 미리보기 ──────────────────────────────────────────────────────────

@router.post("/file/preview")
async def preview_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    파일을 업로드하여 변경될 내용을 미리보기합니다. DB는 변경되지 않습니다.

    Response:
    - to_update       : 변경될 항목 목록 (before/after 포함) → 사용자 확인용
    - skipped         : 처리 불가 항목 (생산투입, QR없음 등)
    - unchanged       : 동일하여 변경 불필요한 건수
    - missing_in_file : DB에는 있으나 파일에 없는 항목 → 삭제 후보
    """
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다.")

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
            "unchanged_count":       result["unchanged"],
            "skipped_count":         len(result["skipped"]),
            "missing_in_file_count": len(result["missing_in_file"]),
            "to_update":             result["to_update"],
            "skipped":               result["skipped"],
            "missing_in_file":       result["missing_in_file"],
        },
    }


# ── 2단계: 확정 반영 ─────────────────────────────────────────────────────────

@router.post("/file/confirm")
async def confirm_upload(body: ConfirmRequest, db: AsyncSession = Depends(get_db)):
    result = await confirm_wip_updates(
        db=db,
        updates=[item.model_dump() for item in body.updates],
        creates=[item.model_dump() for item in body.creates],  # ← 추가
    )
    """
    미리보기에서 사용자가 선택한 항목만 실제로 DB에 반영합니다.

    Request body:
    {
        "updates": [
            { "wip_id": 1, "after": { "material": "SM355A", "thickness": 12.0, ... } },
            ...
        ]
    }
    """
    if not body.updates:
        raise HTTPException(status_code=400, detail="확정할 항목이 없습니다.")

    try:
        result = await confirm_wip_updates(
            db=db,
            updates=[item.model_dump() for item in body.updates],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"확정 처리 중 오류가 발생했습니다: {str(e)}")

    return {
        "status": 200,
        "message": "확정 반영 완료",
        "data": {
            "updated_count": len(result["updated"]),
            "created_count": len(result["created"]),
            "skipped_count": len(result["skipped"]),
            "updated_wip_ids": result["updated"],
            "created_qr_codes": result["created"],  
            "skipped": result["skipped"],
        },
    }


# ── 삭제 ─────────────────────────────────────────────────────────────────────

@router.delete("/file")
async def delete_wips_by_ids(
    wip_ids: List[int],
    db: AsyncSession = Depends(get_db),
):
    """
    미리보기의 missing_in_file 중 사용자가 선택한 항목을 삭제합니다.

    Request body: [1, 2, 3]  (wip_id 리스트)
    """
    if not wip_ids:
        raise HTTPException(status_code=400, detail="삭제할 wip_id 목록이 비어 있습니다.")

    deleted = []
    skipped = []

    for wip_id in wip_ids:
        result = await db.execute(select(SteelWip).where(SteelWip.id == wip_id))
        wip = result.scalars().first()

        if wip is None:
            skipped.append({"wip_id": wip_id, "reason": "존재하지 않는 재공품입니다."})
            continue

        if wip.status in (SteelWipStatus.CONSUMED, SteelWipStatus.RESERVATED):
            skipped.append({
                "wip_id": wip_id,
                "reason": f"이미 생산에 투입된 재고는 삭제할 수 없습니다. (현재 상태: {wip.status.value})",
            })
            continue

        await db.delete(wip)
        deleted.append(wip_id)

    await db.commit()

    return {
        "status": 200,
        "message": "삭제 처리 완료",
        "data": {
            "deleted_count":  len(deleted),
            "skipped_count":  len(skipped),
            "deleted_wip_ids": deleted,
            "skipped":        skipped,
        },
    }