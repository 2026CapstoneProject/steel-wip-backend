# app/crud/wip.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.models import SteelWip
from app.schemas.wip import SteelWipCreate, SteelWipUpdate

async def create_wip(db: AsyncSession, wip_data: SteelWipCreate) -> SteelWip:
    """새로운 재공품 등록 (바코드 발급 등)"""
    db_wip = SteelWip(**wip_data.model_dump())
    db.add(db_wip)
    await db.commit()
    await db.refresh(db_wip)
    return db_wip

async def get_wip_by_id(db: AsyncSession, wip_id: int) -> Optional[SteelWip]:
    """ID로 재공품 단일 조회"""
    result = await db.execute(select(SteelWip).filter(SteelWip.id == wip_id))
    return result.scalars().first()

async def get_wip_by_qr(db: AsyncSession, qr_id: int) -> Optional[SteelWip]:
    """QR ID로 재공품 단일 조회"""
    result = await db.execute(select(SteelWip).filter(SteelWip.qr_id == qr_id))
    return result.scalars().first()

async def get_wips(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[SteelWip]:
    """전체 재공품 목록 단순 조회"""
    result = await db.execute(select(SteelWip).offset(skip).limit(limit))
    return list(result.scalars().all())

async def update_wip(db: AsyncSession, wip_id: int, wip_data: SteelWipUpdate) -> Optional[SteelWip]:
    """재공품 정보 수정 (상태, 위치 변경 등)"""
    db_wip = await get_wip_by_id(db, wip_id)
    if not db_wip:
        return None
        
    # exclude_unset=True를 통해 None으로 들어온 값은 무시하고 변경된 값만 업데이트
    update_data = wip_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_wip, key, value)
        
    await db.commit()
    await db.refresh(db_wip)
    return db_wip

async def delete_wip(db: AsyncSession, wip_id: int) -> bool:
    """재공품 삭제"""
    db_wip = await get_wip_by_id(db, wip_id)
    if not db_wip:
        return False
        
    await db.delete(db_wip)
    await db.commit()
    return True