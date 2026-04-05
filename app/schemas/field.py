# app/schemas/field.py
from pydantic import BaseModel, Field
from typing import List, Optional

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