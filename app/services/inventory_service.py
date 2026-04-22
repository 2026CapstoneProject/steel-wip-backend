# app/services/inventory_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List

from app.models import SteelWip, QrCodes, Locations

async def get_filtered_wips(
    db: AsyncSession,
    qr: Optional[str] = None,
    manufacturer: Optional[str] = None,
    material: Optional[str] = None,
    thickness: Optional[float] = None,
    minWidth: Optional[float] = None,
    maxWidth: Optional[float] = None,
    minLength: Optional[float] = None,
    maxLength: Optional[float] = None
) -> List[dict]:

    # 1. 기본 쿼리: SteelWip + QrCodes(qr_code) + Locations(loc_name) Outer Join
    #    QR 없는 재공품, 위치 미지정 재공품도 모두 포함
    stmt = (
        select(SteelWip, QrCodes.qr_code, Locations.loc_name)
        .outerjoin(QrCodes, SteelWip.qr_id == QrCodes.id)
        .outerjoin(Locations, SteelWip.location_id == Locations.id)
    )

    # 2. 조건이 입력된 경우에만 where 절 동적 추가
    if qr:
        stmt = stmt.where(QrCodes.qr_code == qr)
    if manufacturer:
        stmt = stmt.where(SteelWip.manufacturer == manufacturer)
    if material:
        stmt = stmt.where(SteelWip.material == material)
    if thickness is not None:
        stmt = stmt.where(SteelWip.thickness == thickness)

    # 범위 검색 (min, max)
    if minWidth is not None:
        stmt = stmt.where(SteelWip.width >= minWidth)
    if maxWidth is not None:
        stmt = stmt.where(SteelWip.width <= maxWidth)
    if minLength is not None:
        stmt = stmt.where(SteelWip.length >= minLength)
    if maxLength is not None:
        stmt = stmt.where(SteelWip.length <= maxLength)

    # 3. 비동기 쿼리 실행
    result = await db.execute(stmt)

    # 4. 결과를 딕셔너리 리스트로 변환 (Pydantic 스키마에 맞추기 위함)
    wip_list = []
    for wip_obj, qr_val, loc_name in result.all():
        wip_dict = wip_obj.__dict__.copy()
        wip_dict.pop('_sa_instance_state', None)  # SQLAlchemy 내부 객체 제거
        wip_dict['qr_code_value'] = qr_val
        wip_dict['location_name'] = loc_name      # 위치 이름 (없으면 None)
        wip_list.append(wip_dict)

    return wip_list