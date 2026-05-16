# app/schemas/field.py

from pydantic import BaseModel, Field
from typing import List, Optional


# ─────────────────────────────────────────────
# 공통 서브 스키마
# ─────────────────────────────────────────────

class RelocationBatchItem(BaseModel):
    batchItemId: int
    wipId: int
    wipQr: Optional[str] = None
    manufacturer: Optional[str] = None
    specText: Optional[str] = None
    weightText: Optional[str] = None
    material: str
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    expectedRunningTime: int


class PickingBatchItem(BaseModel):
    batchItemId: int
    wipId: int
    wipQr: Optional[str] = None
    manufacturer: Optional[str] = None
    specText: Optional[str] = None
    weightText: Optional[str] = None
    material: str
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    expectedRunningTime: int = 0
    thickness: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


class InboundBatchItem(BaseModel):
    batchItemId: int
    wipId: int
    wipQr: Optional[str] = None
    manufacturer: Optional[str] = None
    specText: Optional[str] = None
    weightText: Optional[str] = None
    material: str
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    expectedRunningTime: int = 0
    thickness: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


class FieldBatchGroup(BaseModel):
    """배치 한 묶음 — 재배치 목록 + 피킹 목록 + 적재 목록"""
    relocation: List[RelocationBatchItem]
    picking: List[PickingBatchItem]
    inbound: List[InboundBatchItem]


# ─────────────────────────────────────────────
# 작업 완료 화면 (GET /api/field/end)
# ─────────────────────────────────────────────

class FieldEndData(BaseModel):
    """작업 완료 화면 응답 데이터 — 완료된 Batch들만 포함"""
    scenarioId: int
    scenarioTitle: str
    scenarioProgressRate: float   # 0.0 ~ 1.0 (완료 batch_item / 전체 batch_item)
    completedTaskCount: int = 0
    totalTaskCount: int = 0
    remainingTaskCount: int = 0
    batch: List[FieldBatchGroup]  # 완료된 Batch만


class FieldWipDetail(BaseModel):
    qrId: Optional[str] = None
    material: str
    manufacturer: str
    thickness: str
    width: str
    length: str
    weight: str
    ncCode: Optional[str] = None 

class FieldBatchItem(BaseModel):
    scenarioId: Optional[int] = None
    scenarioTitle: Optional[str] = None
    lazerName: Optional[str] = None
    batchId: Optional[int] = None
    batchOrder: Optional[int] = None
    batchItemId: str
    status: str
    batchItemAction: str
    wip: List[FieldWipDetail]
    expectedStartTime: str
    expectedRunningTime: str
    fromLocationName: Optional[str]
    toLocationName: Optional[str]


# ─────────────────────────────────────────────
# 생산 중 화면 (GET /api/field/progress)
# ─────────────────────────────────────────────

class ProgressWipItem(BaseModel):
    """절단 후 발생하는 예상 재공품(estimated_wip) 1개"""
    wipId: int
    batchItemId: Optional[int] = None
    wipQr: Optional[str] = None
    manufacturer: Optional[str] = None
    material: Optional[str] = None
    specText: Optional[str] = None
    weightText: Optional[str] = None
    wipStatus: str
    wipName: str
    toLocation: Optional[str] = None
    status: str

class ProgressLazerCutting(BaseModel):
    """절단 작업 1건 — 투입 재공품 + 발생 예상 재공품 목록"""
    lazerCuttingId: int
    inputWipId: int    # lazer_cutting.steel_wip_id (원자재이면 0)
    material: str      # 투입 재공품의 material
    estimatedCuttingTime: int = 0  # lazer_cutting.estimated_cutting_time (분)
    wip: List[ProgressWipItem]  # 절단 후 발생하는 재공품들


class FieldProgressData(BaseModel):
    """생산 중 화면 응답 — 현재 배치의 절단 작업 전체"""
    scenarioId: int
    scenarioTitle: str
    batchProgressRate: float = 0.0
    completedTaskCount: int = 0
    totalTaskCount: int = 0
    remainingTaskCount: int = 0
    expectedTotalRunningTime: int          # 모든 lazer_cutting.estimated_cutting_time 합산 (분)
    lazer_cutting: List[ProgressLazerCutting]
    hasNoWip: bool = False 
    batchId: Optional[int] = None

# ─────────────────────────────────────────────
# 생산 준비 화면 (GET /api/field/ready)
# ─────────────────────────────────────────────

class FieldReadyData(BaseModel):
    """
    생산 준비 화면 응답 데이터.
    - 현재 시나리오의 모든 Batch(완료 여부 무관)를 포함한다.
    - 다음 시나리오 정보(nextScenarioId, nextScenarioTitle)를 함께 반환한다.
    """
    scenarioId: int
    scenarioTitle: str
    lazerName: Optional[str] = None
    scenarioProgressRate: float          # 0.0 ~ 1.0 (완료 batch_item / 전체 batch_item)
    completedTaskCount: int = 0
    totalTaskCount: int = 0
    remainingTaskCount: int = 0
    currentBatchRemainingTaskCount: int = 0
    currentBatchPendingInboundCount: int = 0
    batch: List[FieldBatchGroup]         # 전체 Batch 목록 (RELOCATE / PICKING 분리)
    nextScenarioId: Optional[int] = None
    nextScenarioTitle: Optional[str] = None


# ─────────────────────────────────────────────
# QR 인식 화면 — GET 응답
# (GET /api/field/{batchItemId}/relocQr|pickingQr|inboundQr)
# ─────────────────────────────────────────────

class QrScanData(BaseModel):
    """
    QR 인식 화면 공통 응답 — 재배치/피킹/적재 3종 GET이 모두 이 스키마를 사용한다.

    - itemScan        : batch_item.item_scanned_at is not None  (잔재 QR 스캔 여부)
    - destinationScan : batch_item.destination_scanned_at is not None  (위치 QR 스캔 여부)
    - height          : DB steel_wip.length 컬럼 (명세서 표기는 height)
    - PICKING의 toLocationName  = scenario.lazer_name  (창고가 아닌 레이저 기기명)
    - INBOUND의 fromLocationName = scenario.lazer_name
    """
    batchItemId: int
    wipId: int
    manufacturer: str                      # steel_wip.manufacturer (제조사)
    material: str
    thickness: float
    width: float
    height: float                          # steel_wip.length
    weight: float                          # steel_wip.weight (중량)
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    itemScan: bool
    destinationScan: bool


# ─────────────────────────────────────────────
# QR 인식 화면 — POST 요청 스키마
# ─────────────────────────────────────────────

class WipQrRequest(BaseModel):
    """잔재 QR 스캔 요청 (POST /{batchItemId}/wipQR)"""
    wipQr: str
    qrAction: str   # "RELOCATION" | "INBOUND" | "PICKING"


class LocQrRequest(BaseModel):
    """위치 QR 스캔 요청 (POST /{batchItemId}/locQR)"""
    locQr: str
    qrAction: str   # "RELOCATION" | "INBOUND" | "PICKING"


class QrSaveRequest(BaseModel):
    """저장 버튼 요청 (POST /{batchItemId}) — 작업 완료 처리.

    - action은 서버가 batch_item.batch_item_action으로 자동 판단한다.
    - 원자재 피킹(steel_wip_id=null)의 경우 wipQR·locQR 모두 null 허용.
    """
    wipQR: Optional[str] = None
    locQR: Optional[str] = None
    thickness: Optional[float] = None   # 작업자 수정 두께
    width: Optional[float] = None       # 작업자 수정 폭
    length: Optional[float] = None      # 작업자 수정 길이


class QrSaveResult(BaseModel):
    batchItemId: int
    action: str
    currentBatchRemainingTaskCount: int = 0
    currentBatchPendingInboundCount: int = 0
    shouldMoveToReady: bool = False
