from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

# ==========================================
# 1. Enum (상태값) 정의
# ==========================================
class UserRole(PyEnum):
    OFFICE = "OFFICE"
    FIELD = "FIELD"

class WipStatus(PyEnum):
    REGISTERED = "REGISTERED"
    IN_STOCK = "IN_STOCK"
    CONSUMED = "CONSUMED"

class ScenarioStatus(PyEnum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class StepStatus(PyEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class StepActionType(PyEnum):
    PICKING = "PICKING"
    NON_PICKING = "NON_PICKING"

class StepItemActionType(PyEnum):
    RELOCATE_FOR_PRODUCTION = "RELOCATE_FOR_PRODUCTION"
    PICKING = "PICKING"
    INBOUND = "INBOUND"
    RELOCATE_FOR_TOMORROW = "RELOCATE_FOR_TOMORROW"

class ActionType(PyEnum):
    RELOCATE_FOR_TODAY = "RELOCATE_FOR_TODAY"
    RELOCATE_FOR_TOMORROW = "RELOCATE_FOR_TOMORROW"
    PICKING = "PICKING"
    INBOUND = "INBOUND"

class LazerType(PyEnum):
    LAZER1 = "LAZER1"
    LAZER2 = "LAZER2"
    LAZER3 = "LAZER3"

# ==========================================
# 2. Base 클래스 정의
# ==========================================
class Base(DeclarativeBase):
    pass

# ==========================================
# 3. 데이터베이스 테이블 모델 정의
# ==========================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50))
    department: Mapped[str] = mapped_column(String(50), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))
    user_num: Mapped[int] = mapped_column(Integer, unique=True)

class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loc_name: Mapped[str] = mapped_column(String(100))
    loc_stack_height: Mapped[int] = mapped_column(Integer, default=0)

class SteelWip(Base):
    __tablename__ = "steel_wip"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[WipStatus] = mapped_column(Enum(WipStatus), default=WipStatus.REGISTERED)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=True)
    material: Mapped[str] = mapped_column(String(100), nullable=True)
    thickness: Mapped[float] = mapped_column(Float, nullable=True)
    width: Mapped[float] = mapped_column(Float, nullable=True)
    length: Mapped[float] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=True)
    
    # 생산 라인 투입 시 null 가능하므로 nullable=True
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=True)
    stack_level: Mapped[int] = mapped_column(Integer, nullable=True)

class SteelWipHistory(Base):
    __tablename__ = "steel_wip_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steel_wip_id: Mapped[int] = mapped_column(ForeignKey("steel_wip.id"))
    history_location: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    history_loc_time: Mapped[datetime] = mapped_column(DateTime, default=func.now())

class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[ScenarioStatus] = mapped_column(Enum(ScenarioStatus), default=ScenarioStatus.DRAFT)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    ordered_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    lazer_name: Mapped[LazerType] = mapped_column(Enum(LazerType), nullable=True)

class ScenarioStep(Base):
    __tablename__ = "scenario_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    step_order: Mapped[int] = mapped_column(Integer)
    step_action: Mapped[StepActionType] = mapped_column(Enum(StepActionType))
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class StepItem(Base):
    __tablename__ = "step_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("scenario_steps.id"))
    steel_wip_id: Mapped[int] = mapped_column(ForeignKey("steel_wip.id"))
    
    step_item_order: Mapped[int] = mapped_column(Integer)
    step_item_action: Mapped[StepItemActionType] = mapped_column(Enum(StepItemActionType))
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.PENDING)

    from_location: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=True)
    to_location: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=True)
    
    item_scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    source_scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    destination_scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class LazerCutting(Base):
    __tablename__ = "lazer_cutting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    steel_wip_id: Mapped[int] = mapped_column(ForeignKey("steel_wip.id"))
    
    estimated_cutting_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    real_cutting_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class EstimatedWip(Base):
    __tablename__ = "estimated_wips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lazer_cutting_id: Mapped[int] = mapped_column(ForeignKey("lazer_cutting.id"))
    
    estimated_wip_thickness: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_wip_width: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_wip_length: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_wip_weight: Mapped[float] = mapped_column(Float, nullable=True)
