# schemas/batch_item.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from schemas.enums import BatchItemStatus, BatchActionType

class BatchItemBase(BaseModel):
    batch_id: int
    steel_wip_id: Optional[int] = None
    batch_item_order: Optional[int] = None
    batch_item_action: BatchActionType
    status: BatchItemStatus = BatchItemStatus.PENDING
    from_location: Optional[int] = None
    to_location: Optional[int] = None
    expected_start_time: int = 0
    expected_running_time: int = 0

class BatchItemCreate(BatchItemBase):
    pass

class BatchItemUpdate(BaseModel):
    status: Optional[BatchItemStatus] = None
    from_location: Optional[int] = None
    to_location: Optional[int] = None
    item_scanned_at: Optional[datetime] = None
    destination_scanned_at: Optional[datetime] = None

class BatchItemResponse(BatchItemBase):
    id: int
    item_scanned_at: Optional[datetime] = None
    destination_scanned_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)