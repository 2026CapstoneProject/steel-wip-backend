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
    RELOCATE = "RELOCATE"
    PICKING = "PICKING"
    INBOUND = "INBOUND"

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