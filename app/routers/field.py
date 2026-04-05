# app/routers/field.py

from fastapi import APIRouter, Depends, Query

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.field import (
    FieldEndData, FieldBatchItem, FieldProgressData, FieldReadyData,
    QrScanData, WipQrRequest, LocQrRequest, QrSaveRequest,
)

from app.services import field_service

router = APIRouter()


# ─────────────────────────────────────────────
# GET /api/field/end  —  작업 완료 화면
# ─────────────────────────────────────────────
@router.get("/end", response_model=BaseResponse[List[FieldEndData]])
async def get_field_end(
    batchId: int = Query(..., description="방금 완료된 Batch ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    작업 완료 화면 조회

    - 완료된 Batch들의 재배치 / 피킹 내역을 반환합니다.
    - 시나리오 전체 진행률(scenarioProgressRate)도 함께 반환합니다.

    **참고:** 원래 명세서는 GET + Request Body 구조였으나,
    HTTP 표준에 맞게 Query Parameter(?batchId=)로 변경하였습니다.
    """
    data = await field_service.get_field_end(db, batchId)
    return BaseResponse(
        status=200,
        message="현장 생산 완료 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# GET /api/field/progress  —  생산 중 화면
# ─────────────────────────────────────────────
# ⚠️ 주의: /{lazer_name} 캐치올 라우터보다 반드시 먼저 선언되어야 함
@router.get("/progress", response_model=BaseResponse[List[FieldProgressData]])
async def get_field_progress(
    db: AsyncSession = Depends(get_db),
):
    """
    생산 중 화면 조회

    - 현재 시나리오(최소 scenario_order)의 첫 번째 배치(최소 batch_order)를 기준으로,
    - 해당 배치의 lazer_cutting 목록과 절단 후 발생할 예상 재공품(estimated_wips)을 반환합니다.
    - 각 예상 재공품의 INBOUND 작업 상태("적재 대기" / "적재 완료")도 함께 반환합니다.
    - expectedTotalRunningTime: 모든 lazer_cutting.estimated_cutting_time 합산 (분)
    """
    data = await field_service.get_field_progress(db)
    return BaseResponse(
        status=200,
        message="현장 생산 중 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# GET /api/field/ready  —  생산 준비 화면
# ─────────────────────────────────────────────
# ⚠️ 주의: /{lazer_name} 캐치올 라우터보다 반드시 먼저 선언되어야 함
@router.get("/ready", response_model=BaseResponse[List[FieldReadyData]])
async def get_field_ready(
    db: AsyncSession = Depends(get_db),
):
    """
    생산 준비 화면 조회

    - 현재 시나리오(최소 scenario_order)의 전체 Batch 목록과 각 Batch의 작업(RELOCATE / PICKING)을 반환합니다.
    - 완료 여부와 무관하게 모든 Batch를 포함합니다.
    - scenarioProgressRate: 전체 batch_item 중 COMPLETED 비율
    - nextScenarioId / nextScenarioTitle: 다음 시나리오 정보 (없으면 null)
    """
    data = await field_service.get_field_ready(db)
    return BaseResponse(
        status=200,
        message="현장 생산 준비 정보 조회에 성공했습니다.",
        data=data,
    )


# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/relocQr  —  재배치 QR 화면
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/relocQr", response_model=BaseResponse[List[QrScanData]])
async def get_reloc_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    """
    재배치 QR 화면 조회.
    잔재 상세 정보 및 from/to 위치, Poka-Yoke 스캔 진행 상황(itemScan·destinationScan)을 반환한다.
    """
    data = await field_service.get_reloc_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/pickingQr  —  피킹 QR 화면
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/pickingQr", response_model=BaseResponse[List[QrScanData]])
async def get_picking_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    """
    피킹 QR 화면 조회.
    toLocationName은 레이저 기기명(scenario.lazer_name)으로 반환한다.
    """
    data = await field_service.get_picking_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# GET /api/field/{batchItemId}/inboundQr  —  적재 QR 화면
# ─────────────────────────────────────────────
@router.get("/{batch_item_id}/inboundQr", response_model=BaseResponse[List[QrScanData]])
async def get_inbound_qr(batch_item_id: int, db: AsyncSession = Depends(get_db)):
    """
    적재 QR 화면 조회.
    fromLocationName은 레이저 기기명(scenario.lazer_name)으로 반환한다.
    """
    data = await field_service.get_inbound_qr(db, batch_item_id)
    return BaseResponse(status=200, message="현장 스캔 정보 조회에 성공했습니다.", data=data)


# ─────────────────────────────────────────────
# POST /api/field/{batchItemId}/wipQR  —  잔재 QR 스캔
# ─────────────────────────────────────────────
@router.post("/{batch_item_id}/wipQR", response_model=BaseResponse[None])
async def scan_wip_qr(batch_item_id: int, req: WipQrRequest, db: AsyncSession = Depends(get_db)):
    """
    잔재 QR 스캔. Poka-Yoke 검증 후 item_scanned_at 기록.
    qrAction: "RELOCATION" | "INBOUND" | "PICKING"
    """
    await field_service.scan_wip_qr(db, batch_item_id, req)
    return BaseResponse(status=200, message="잔재 QR 스캔이 완료되었습니다.", data=None)


# ─────────────────────────────────────────────
# POST /api/field/{batchItemId}/locQR  —  위치 QR 스캔
# ─────────────────────────────────────────────
@router.post("/{batch_item_id}/locQR", response_model=BaseResponse[None])
async def scan_loc_qr(batch_item_id: int, req: LocQrRequest, db: AsyncSession = Depends(get_db)):
    """
    위치 QR 스캔. Poka-Yoke 검증 후 destination_scanned_at 기록.
    PICKING은 to_location=null이므로 위치 검증 생략.
    qrAction: "RELOCATION" | "INBOUND" | "PICKING"
    """
    await field_service.scan_loc_qr(db, batch_item_id, req)
    return BaseResponse(status=200, message="위치 QR 스캔이 완료되었습니다.", data=None)


# ─────────────────────────────────────────────
# POST /api/field/{batchItemId}  —  저장 (작업 완료 처리)
# ─────────────────────────────────────────────
@router.post("/{batch_item_id}", response_model=BaseResponse[None])
async def save_qr_action(batch_item_id: int, req: QrSaveRequest, db: AsyncSession = Depends(get_db)):
    """
    저장 버튼 클릭 — 작업 완료 처리.
    wipQR/locQR 재검증 → batch_item COMPLETED → steel_wip 위치/상태 업데이트.
    action: "RELOCATION" | "INBOUND" | "PICKING"
    """
    await field_service.save_qr_action(db, batch_item_id, req)
    return BaseResponse(status=200, message="작업이 완료 처리되었습니다.", data=None)


# ─────────────────────────────────────────────
# GET /api/field/{lazer_name}  —  실시간 현장 정보
# ─────────────────────────────────────────────
# ⚠️ 주의: 경로 파라미터 캐치올 라우터이므로 반드시 최하단에 위치해야 함
@router.get("/{lazer_name}", response_model=BaseResponse[List[FieldBatchItem]])
async def get_live_field_dashboard(
    lazer_name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    실시간 현장 정보 조회
    - 특정 레이저(lazer_name)에 할당된 진행 중인 시나리오의 첫 번째 Batch 안에서,
    - PENDING, IN_PROGRESS 상태인 작업 지시(BatchItem)만 시간순으로 필터링하여 반환합니다.
    """
    data = await field_service.get_live_field_data(db, lazer_name)

    return BaseResponse(
        status=200,
        message="현장 실시간 정보 조회에 성공했습니다.",
        data=data
    )
