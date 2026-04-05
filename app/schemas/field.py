# app/schemas/field.py

from pydantic import BaseModel, Field
from typing import List, Optional


# ─────────────────────────────────────────────
# 공통 서브 스키마
# ─────────────────────────────────────────────

class RelocationBatchItem(BaseModel):
    """재배치(RELOCATE) 작업 아이템"""
    batchItemId: int
    wipId: int
    material: str
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    expectedRunningTime: int


class PickingBatchItem(BaseModel):
    """
    피킹(PICKING) 작업 아이템.
    - wipId == 0 이면 원자재 (규격 필드 사용)
    - wipId > 0  이면 재공품 (위치 필드 사용)
    """
    batchItemId: int
    wipId: int
    material: str
    fromLocationName: Optional[str] = None
    toLocationName: Optional[str] = None
    # 원자재인 경우에만 채워지는 필드
    thickness: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None  # DB의 length 컬럼에 대응


class FieldBatchGroup(BaseModel):
    """배치 한 묶음 — 재배치 목록 + 피킹 목록"""
    relocation: List[RelocationBatchItem]
    picking: List[PickingBatchItem]


# ─────────────────────────────────────────────
# 작업 완료 화면 (GET /api/field/end)
# ─────────────────────────────────────────────

class FieldEndData(BaseModel):
    """작업 완료 화면 응답 데이터 — 완료된 Batch들만 포함"""
    scenarioId: int
    scenarioTitle: str
    scenarioProgressRate: float   # 0.0 ~ 1.0 (완료 batch_item / 전체 batch_item)
    batch: List[FieldBatchGroup]  # 완료된 Batch만


class FieldWipDetail(BaseModel):
    qrId: str
    material: str
    manufacturer: str
    thickness: str
    width: str
    length: str
    weight: str

class FieldBatchItem(BaseModel):
    batchItemId: str
    status: str
    batchItemAction: str
    wip: List[FieldWipDetail]
    expectedStartTime: str
    expectedRunningTime: str
    fromLocationName: Optional[str]
    toLocationName: Optional[str]
