# schemas/enums.py
from enum import Enum

class UserRole(str, Enum):
    OFFICE = "OFFICE"
    FIELD = "FIELD"

class WipStatus(str, Enum):
    REGISTERED = "REGISTERED"
    IN_STOCK = "IN_STOCK"
    RESERVATED = "RESERVATED"
    CONSUMED = "CONSUMED"
    RAW_MATERIAL = "RAW_MATERIAL"

class ScenarioStatus(str, Enum):
    DRAFT = "DRAFT"
    ORDERED = "ORDERED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class BatchItemStatus(str, Enum):
    BEFORE_PENDING = "BEFORE_PENDING"
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class BatchActionType(str, Enum):
    RELOCATE     = "RELOCATE"
    PICKING      = "PICKING"
    INBOUND      = "INBOUND"
    TEMP_MOVE    = "TEMP_MOVE"
    RESTORE      = "RESTORE"
    DIRECT_START = "DIRECT_START"  # 원자재 투입


# ── 작업 타입별 아이콘, 한글명, 색상 매핑 ──────────────────────────
BATCH_ACTION_METADATA = {
    "RELOCATE": {
        "label_ko": "재배치",
        "icon": "🔄",
        "color": "#9CA3AF",  # 회색
        "material_icon": "sync_alt",
    },
    "PICKING": {
        "label_ko": "피킹",
        "icon": "🎯",
        "color": "#EF4444",  # 빨강
        "material_icon": "location_on",
    },
    "INBOUND": {
        "label_ko": "적재",
        "icon": "📦",
        "color": "#06B6D4",  # 청색
        "material_icon": "publish",
    },
    "TEMP_MOVE": {
        "label_ko": "임시이동",
        "icon": "↪️",
        "color": "#F97316",  # 주황
        "material_icon": "arrow_forward",
    },
    "RESTORE": {
        "label_ko": "원상복구",
        "icon": "↩️",
        "color": "#06B6D4",  # 청색
        "material_icon": "arrow_back",
    },
    "DIRECT_START": {
        "label_ko": "원자재 투입",
        "icon": "⬜",
        "color": "#A855F7",  # 보라
        "material_icon": "input",
    },
}

class LazerType(str, Enum):
    LAZER1 = "LAZER1"
    LAZER2 = "LAZER2"
    LAZER3 = "LAZER3"

class CuttingStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class CuttingPriority(str, Enum):
    LOW = "LOW"
    MIDDLE = "MIDDLE"
    HIGH = "HIGH"