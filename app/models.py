from typing import Optional
import datetime
import enum

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKeyConstraint, Index, Integer, String, TIMESTAMP, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sqlalchemy import UniqueConstraint

class Base(DeclarativeBase):
    pass


class BatchItemsBatchItemAction(str, enum.Enum):
    RELOCATE = 'RELOCATE'
    PICKING = 'PICKING'
    INBOUND = 'INBOUND'


class BatchItemsStatus(str, enum.Enum):
    BEFORE_PENDING = 'BEFORE_PENDING'
    PENDING = 'PENDING'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'


class LazerCuttingPriority(str, enum.Enum):
    LOW = 'LOW'
    MIDDLE = 'MIDDLE'
    HIGH = 'HIGH'


class LazerCuttingStatus(str, enum.Enum):
    PENDING = 'PENDING'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'


class ScenariosLazerName(str, enum.Enum):
    LAZER1 = 'LAZER1'
    LAZER2 = 'LAZER2'
    LAZER3 = 'LAZER3'


class ScenariosStatus(str, enum.Enum):
    LANTEK_IMPORTED = 'LANTEK_IMPORTED' 
    DRAFT = 'DRAFT'
    ORDERED = 'ORDERED'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'


class SteelWipStatus(str, enum.Enum):
    RAW_MATERIAL = 'RAW_MATERIAL'   # ← 추가: LANTEK 가져오기 시 원자재 임시 등록
    REGISTERED = 'REGISTERED'
    IN_STOCK = 'IN_STOCK'
    RESERVATED = 'RESERVATED'
    CONSUMED = 'CONSUMED'

class UsersRole(str, enum.Enum):
    OFFICE = 'OFFICE'
    FIELD = 'FIELD'


class Locations(Base):
    __tablename__ = 'locations'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loc_name: Mapped[Optional[str]] = mapped_column(String(255))
    loc_can_stock: Mapped[Optional[int]] = mapped_column(TINYINT(1))
    loc_stack_height: Mapped[Optional[int]] = mapped_column(Integer)

    steel_wip: Mapped[list['SteelWip']] = relationship('SteelWip', back_populates='location')
    steel_wip_history: Mapped[list['SteelWipHistory']] = relationship('SteelWipHistory', back_populates='locations')
    batch_items_from_location: Mapped[list['BatchItems']] = relationship('BatchItems', foreign_keys='[BatchItems.from_location]', back_populates='locations')
    batch_items_to_location: Mapped[list['BatchItems']] = relationship('BatchItems', foreign_keys='[BatchItems.to_location]', back_populates='locations_')


class Projects(Base):
    __tablename__ = 'projects'
    __table_args__ = (
        Index('ix_projects_title', 'title', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    project_due: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    scenarios: Mapped[list['Scenarios']] = relationship('Scenarios', back_populates='project')


class QrCodes(Base):
    __tablename__ = 'qr_codes'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qr_code: Mapped[Optional[str]] = mapped_column(String(255))

    steel_wip: Mapped[list['SteelWip']] = relationship('SteelWip', back_populates='qr')
    estimated_wips: Mapped[list['EstimatedWips']] = relationship('EstimatedWips', back_populates='qr')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        UniqueConstraint('username', name='uq_users_username'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)   # ← 추가
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UsersRole] = mapped_column(Enum(UsersRole, values_callable=lambda cls: [member.value for member in cls]), nullable=False)
    user_num: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('(now())'))   # ← 추가
    last_login_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, nullable=True)                 # ← 추가

    scenarios_assignee: Mapped[list['Scenarios']] = relationship('Scenarios', foreign_keys='[Scenarios.assignee_id]', back_populates='assignee')
    scenarios_creator: Mapped[list['Scenarios']] = relationship('Scenarios', foreign_keys='[Scenarios.creator_id]', back_populates='creator')
class Scenarios(Base):
    __tablename__ = 'scenarios'
    __table_args__ = (
        ForeignKeyConstraint(['assignee_id'], ['users.id'], name='scenarios_ibfk_3'),
        ForeignKeyConstraint(['creator_id'], ['users.id'], name='scenarios_ibfk_2'),
        ForeignKeyConstraint(['project_id'], ['projects.id'], name='scenarios_ibfk_1'),
        Index('ix_scenarios_assignee_id', 'assignee_id'),
        Index('ix_scenarios_creator_id', 'creator_id'),
        Index('ix_scenarios_project_id', 'project_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    scenario_due: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    scenario_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('(now())'))
    ordered_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    lazer_name: Mapped[Optional[ScenariosLazerName]] = mapped_column(Enum(ScenariosLazerName, values_callable=lambda cls: [member.value for member in cls]))
    project_id: Mapped[Optional[int]] = mapped_column(Integer)
    creator_id: Mapped[Optional[int]] = mapped_column(Integer)
    assignee_id: Mapped[Optional[int]] = mapped_column(Integer)
    emergency_or_not: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))

    assignee: Mapped[Optional['Users']] = relationship('Users', foreign_keys=[assignee_id], back_populates='scenarios_assignee')
    creator: Mapped[Optional['Users']] = relationship('Users', foreign_keys=[creator_id], back_populates='scenarios_creator')
    project: Mapped[Optional['Projects']] = relationship('Projects', back_populates='scenarios')
    batch: Mapped[list['Batch']] = relationship('Batch', back_populates='scenario')
    lazer_cutting: Mapped[list['LazerCutting']] = relationship('LazerCutting', back_populates='scenario')


class SteelWip(Base):
    __tablename__ = 'steel_wip'
    __table_args__ = (
        ForeignKeyConstraint(['location_id'], ['locations.id'], name='steel_wip_ibfk_1'),
        ForeignKeyConstraint(['qr_id'], ['qr_codes.id'], name='steel_wip_ibfk_2'),
        Index('ix_steel_wip_location_id', 'location_id'),
        Index('ix_steel_wip_qr_id', 'qr_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[SteelWipStatus] = mapped_column(Enum(SteelWipStatus, values_callable=lambda cls: [member.value for member in cls]), nullable=False, server_default=text("'REGISTERED'"), comment='재공품 현재 상태')
    material: Mapped[str] = mapped_column(String(255), nullable=False)
    thickness: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    length: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255))
    location_id: Mapped[Optional[int]] = mapped_column(Integer, comment='현재 위치 (생산 라인 투입 시 null 처리 가능)')
    stack_level: Mapped[Optional[int]] = mapped_column(Integer, comment='현재 적재 층')
    qr_id: Mapped[Optional[int]] = mapped_column(Integer)

    location: Mapped[Optional['Locations']] = relationship('Locations', back_populates='steel_wip')
    qr: Mapped[Optional['QrCodes']] = relationship('QrCodes', back_populates='steel_wip')
    steel_wip_history: Mapped[list['SteelWipHistory']] = relationship('SteelWipHistory', back_populates='steel_wip')
    batch_items: Mapped[list['BatchItems']] = relationship('BatchItems', back_populates='steel_wip')
    lazer_cutting: Mapped[list['LazerCutting']] = relationship('LazerCutting', back_populates='steel_wip')


class Batch(Base):
    __tablename__ = 'batch'
    __table_args__ = (
        ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], name='batch_ibfk_1'),
        Index('ix_batch_scenario_id', 'scenario_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_order: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)

    scenario: Mapped['Scenarios'] = relationship('Scenarios', back_populates='batch')
    batch_items: Mapped[list['BatchItems']] = relationship('BatchItems', back_populates='batch')
    lazer_cutting: Mapped[list['LazerCutting']] = relationship('LazerCutting', back_populates='batch')


class SteelWipHistory(Base):
    __tablename__ = 'steel_wip_history'
    __table_args__ = (
        ForeignKeyConstraint(['history_location'], ['locations.id'], name='steel_wip_history_ibfk_2'),
        ForeignKeyConstraint(['steel_wip_id'], ['steel_wip.id'], name='steel_wip_history_ibfk_1'),
        Index('ix_steel_wip_history_history_location', 'history_location'),
        Index('ix_steel_wip_history_steel_wip_id', 'steel_wip_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    steel_wip_id: Mapped[int] = mapped_column(Integer, nullable=False)
    history_stack_level: Mapped[int] = mapped_column(Integer, nullable=False)
    history_stack_height: Mapped[int] = mapped_column(Integer, nullable=False)
    history_location: Mapped[Optional[int]] = mapped_column(Integer)
    history_loc_time: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)

    locations: Mapped[Optional['Locations']] = relationship('Locations', back_populates='steel_wip_history')
    steel_wip: Mapped['SteelWip'] = relationship('SteelWip', back_populates='steel_wip_history')


class BatchItems(Base):
    __tablename__ = 'batch_items'
    __table_args__ = (
        ForeignKeyConstraint(['batch_id'], ['batch.id'], name='batch_items_ibfk_1'),
        ForeignKeyConstraint(['from_location'], ['locations.id'], name='batch_items_ibfk_3'),
        ForeignKeyConstraint(['steel_wip_id'], ['steel_wip.id'], name='batch_items_ibfk_2'),
        ForeignKeyConstraint(['to_location'], ['locations.id'], name='batch_items_ibfk_4'),
        Index('ix_batch_items_batch_id', 'batch_id'),
        Index('ix_batch_items_from_location', 'from_location'),
        Index('ix_batch_items_steel_wip_id', 'steel_wip_id'),
        Index('ix_batch_items_to_location', 'to_location')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_item_action: Mapped[BatchItemsBatchItemAction] = mapped_column(Enum(BatchItemsBatchItemAction, values_callable=lambda cls: [member.value for member in cls]), nullable=False)
    status: Mapped[BatchItemsStatus] = mapped_column(Enum(BatchItemsStatus, values_callable=lambda cls: [member.value for member in cls]), nullable=False, server_default=text("'PENDING'"))
    steel_wip_id: Mapped[Optional[int]] = mapped_column(Integer)
    batch_item_order: Mapped[Optional[int]] = mapped_column(Integer)
    from_location: Mapped[Optional[int]] = mapped_column(Integer, comment='INBOUND 시 null')
    to_location: Mapped[Optional[int]] = mapped_column(Integer, comment='PICK_OUT 시 null')
    expected_start_time: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    expected_running_time: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    item_scanned_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, comment='개별 재공품 스캔 (모든 작업 공통 기록)')
    destination_scanned_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, comment='도착 구역 스캔 (RELOCATE, INBOUND 시 기록)')

    batch: Mapped['Batch'] = relationship('Batch', back_populates='batch_items')
    locations: Mapped[Optional['Locations']] = relationship('Locations', foreign_keys=[from_location], back_populates='batch_items_from_location')
    steel_wip: Mapped[Optional['SteelWip']] = relationship('SteelWip', back_populates='batch_items')
    locations_: Mapped[Optional['Locations']] = relationship('Locations', foreign_keys=[to_location], back_populates='batch_items_to_location')


class LazerCutting(Base):
    __tablename__ = 'lazer_cutting'
    __table_args__ = (
        ForeignKeyConstraint(['batch_id'], ['batch.id'], name='lazer_cutting_ibfk_3'),
        ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], name='lazer_cutting_ibfk_1'),
        ForeignKeyConstraint(['steel_wip_id'], ['steel_wip.id'], name='lazer_cutting_ibfk_2'),
        Index('ix_lazer_cutting_batch_id', 'batch_id'),
        Index('ix_lazer_cutting_scenario_id', 'scenario_id'),
        Index('ix_lazer_cutting_steel_wip_id', 'steel_wip_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[Optional[int]] = mapped_column(Integer, comment='시나리오 status == DRAFT인 경우 해당 테이블을 통해 WIP과 M:N 관계')
    priority: Mapped[Optional[LazerCuttingPriority]] = mapped_column(Enum(LazerCuttingPriority, values_callable=lambda cls: [member.value for member in cls]), server_default=text("'LOW'"))
    estimated_cutting_time: Mapped[Optional[int]] = mapped_column(Integer)
    real_cutting_time: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[Optional[LazerCuttingStatus]] = mapped_column(Enum(LazerCuttingStatus, values_callable=lambda cls: [member.value for member in cls]), server_default=text("'PENDING'"))
    steel_wip_id: Mapped[Optional[int]] = mapped_column(Integer)
    batch_id: Mapped[Optional[int]] = mapped_column(Integer)
    
    nc_code: Mapped[Optional[str]] = mapped_column(String(255))
    input_material: Mapped[Optional[str]] = mapped_column(String(255))   # 판재 재질
    input_width: Mapped[Optional[float]] = mapped_column(Float)           # 판재 폭 (PDF 파싱값)
    input_length: Mapped[Optional[float]] = mapped_column(Float)          # 판재 길이 (PDF 파싱값)

    batch: Mapped[Optional['Batch']] = relationship('Batch', back_populates='lazer_cutting')
    scenario: Mapped[Optional['Scenarios']] = relationship('Scenarios', back_populates='lazer_cutting')
    steel_wip: Mapped[Optional['SteelWip']] = relationship('SteelWip', back_populates='lazer_cutting')
    estimated_wips: Mapped[list['EstimatedWips']] = relationship('EstimatedWips', back_populates='lazer_cutting')


class EstimatedWips(Base):
    __tablename__ = 'estimated_wips'
    __table_args__ = (
        ForeignKeyConstraint(['lazer_cutting_id'], ['lazer_cutting.id'], name='estimated_wips_ibfk_1'),
        ForeignKeyConstraint(['qr_id'], ['qr_codes.id'], name='estimated_wips_ibfk_2'),
        Index('ix_estimated_wips_lazer_cutting_id', 'lazer_cutting_id'),
        Index('ix_estimated_wips_qr_id', 'qr_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lazer_cutting_id: Mapped[Optional[int]] = mapped_column(Integer)
    qr_id: Mapped[Optional[int]] = mapped_column(Integer)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255))
    material: Mapped[Optional[str]] = mapped_column(String(255))
    thickness: Mapped[Optional[float]] = mapped_column(Float)
    width: Mapped[Optional[float]] = mapped_column(Float)
    length: Mapped[Optional[float]] = mapped_column(Float)
    weight: Mapped[Optional[float]] = mapped_column(Float)

    lazer_cutting: Mapped[Optional['LazerCutting']] = relationship('LazerCutting', back_populates='estimated_wips')
    qr: Mapped[Optional['QrCodes']] = relationship('QrCodes', back_populates='estimated_wips')

class TokenBlacklist(Base):
    __tablename__ = 'token_blacklist'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jti: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # JWT ID
    expired_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)  # 토큰 만료시각
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('(now())'))
    