# app/routers/field.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.field import (
    FieldEndData, FieldBatchItem, FieldProgressData, FieldReadyData,
    QrScanData, QrSaveRequest, QrSaveResult,
)
from app.services import field_service

router = APIRouter()


# ─────────────────────────────────────────────
# GET /api/field/end  —  작업 완료 화면
# ─────────────────────────────────────────────
@router.get("/end", response_model=BaseResponse[List[FieldEndData]])
async def get_field_end(
    batchId: Optional[int] = Query(None, description="완료된 Batch ID (생략 시 전체 완료 아이템 반환)"),
    db: AsyncSession = Depends(get_db),
):
    data = await field_service.get_field_end(db, batchId)
    return BaseResponse(
        status=200,
        message="현장 생산 완료 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# GET /api/field/progress  —  생산 중 화면
# ─────────────────────────────────────────────
@router.get("/progress", response_model=BaseResponse[List[FieldProgressData]])
async def get_field_progress(
    db: AsyncSession = Depends(get_db),
):
    data = await field_service.get_field_progress(db)
    return BaseResponse(
        status=200,
        message="현장 생산 중 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# GET /api/field/ready  —  생산 준비 화면
# ─────────────────────────────────────────────
@router.get("/ready", response_model=BaseResponse[List[FieldReadyData]])
async def get_field_ready(
    db: AsyncSession = Depends(get_db),
):
    data = await field_service.get_field_ready(db)
    return BaseResponse(
        status=200,
        message="현장 생산 준비 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# POST /api/field/scenario/{scenario_id}/complete  —  시나리오 완료 처리
# ─────────────────────────────────────────────
# ⚠️ 주의: /{batch_item_id} 캐치올 라우터보다 반드시 먼저 선언되어야 함
@router.post("/scenario/{scenario_id}/complete", response_model=BaseResponse)
async def complete_scenario(
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    시나리오 작업 완료 처리.
    - 해당 시나리오의 scenario_order를 0으로 설정 → /app/start에서 미노출
    - 나머지 발행된 시나리오들의 scenario_order를 1씩 감소
    """
    await field_service.complete_scenario(db, scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오가 완료 처리되었습니다.",
        data=None,
    )

# ─────────────────────────────────────────────
# POST /api/field/scenario/{scenario_id}/complete  —  시나리오 - Batch 생산 완료 처리
# ─────────────────────────────────────────────
@router.post("/batch/{batch_id}/complete")
async def complete_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    """재공품 없는 Batch의 수동 생산완료 처리"""
    result = await field_service.complete_batch_manually(db, batch_id)
    return {"status": 200, "message": "생산완료 처리가 완료되었습니다.", "data": result}

# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/relocQr
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/relocQr", response_model=BaseResponse[List[QrScanData]])
async def get_reloc_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    data = await field_service.get_reloc_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/pickingQr
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/pickingQr", response_model=BaseResponse[List[QrScanData]])
async def get_picking_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    data = await field_service.get_picking_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/inboundQr
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/inboundQr", response_model=BaseResponse[List[QrScanData]])
async def get_inbound_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    data = await field_service.get_inbound_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# POST /api/field/{batchItemId}  —  저장 (작업 완료 처리)
# ─────────────────────────────────────────────
@router.post("/{batch_item_id}", response_model=BaseResponse[QrSaveResult])
async def save_qr_action(batch_item_id: int, req: QrSaveRequest, db: AsyncSession = Depends(get_db)):
    result = await field_service.save_qr_action(db, batch_item_id, req)
    return BaseResponse(status=200, message="작업이 완료 처리되었습니다.", data=result)


# ─────────────────────────────────────────────
# GET /api/field/{lazer_name}  —  실시간 현장 정보
# ─────────────────────────────────────────────
# ⚠️ 경로 파라미터 캐치올이므로 반드시 최하단에 위치
@router.get("/{lazer_name}", response_model=BaseResponse[List[FieldBatchItem]])
async def get_live_field_dashboard(
    lazer_name: str,
    db: AsyncSession = Depends(get_db)
):
    data = await field_service.get_live_field_data(db, lazer_name)
    return BaseResponse(
        status=200,
        message="현장 실시간 정보 조회에 성공했습니다.",
        data=data
    )
