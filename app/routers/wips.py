# app/routers/wips.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database import get_db
from app.crud import wip as wip_crud
from app.schemas import SteelWipCreate, SteelWipUpdate, SteelWipResponse, BaseResponse
from app.services import inventory_service
from app.schemas.wip import SteelWipWithQrResponse


router = APIRouter()

@router.post("", response_model=BaseResponse[SteelWipResponse])
async def create_steel_wip(wip: SteelWipCreate, db: AsyncSession = Depends(get_db)):
    if wip.qr_id:
        existing_wip = await wip_crud.get_wip_by_qr(db, wip.qr_id)
        if existing_wip:
            raise HTTPException(status_code=400, detail="이미 등록된 QR 코드입니다.")
            
    new_wip = await wip_crud.create_wip(db, wip)
    return BaseResponse(status=201, message="재공품 등록 성공", data=new_wip)

@router.get("/{wip_id}", response_model=BaseResponse[SteelWipResponse])
async def get_steel_wip(wip_id: int, db: AsyncSession = Depends(get_db)):
    wip = await wip_crud.get_wip_by_id(db, wip_id)
    if not wip:
        raise HTTPException(status_code=404, detail="재공품을 찾을 수 없습니다.")
    return BaseResponse(status=200, message="재공품 조회 성공", data=wip)

@router.get("", response_model=BaseResponse[List[SteelWipWithQrResponse]])
async def get_wip_inventory(
    db: AsyncSession = Depends(get_db),
    qr: Optional[str] = Query(None, description="QR 코드 값"),
    manufacturer: Optional[str] = Query(None, description="제조사 (예: POSCO)"),
    material: Optional[str] = Query(None, description="재질 (예: SM355A)"),
    thickness: Optional[float] = Query(None, description="두께"),
    minWidth: Optional[float] = Query(None, description="최소 폭"),
    maxWidth: Optional[float] = Query(None, description="최대 폭"),
    minLength: Optional[float] = Query(None, description="최소 길이"),
    maxLength: Optional[float] = Query(None, description="최대 길이")
):
    """
    재고 현황 조회 (다중 필터링 지원)
    - 아무 파라미터를 넣지 않으면 전체 재고를 반환합니다.
    """
    results = await inventory_service.get_filtered_wips(
        db=db,
        qr=qr,
        manufacturer=manufacturer,
        material=material,
        thickness=thickness,
        minWidth=minWidth,
        maxWidth=maxWidth,
        minLength=minLength,
        maxLength=maxLength
    )
    
    return BaseResponse(
        status=200,
        message="재고 조회에 성공했습니다.",
        data=results
    )

@router.patch("/{wip_id}", response_model=BaseResponse[SteelWipResponse])
async def update_steel_wip(wip_id: int, wip: SteelWipUpdate, db: AsyncSession = Depends(get_db)):
    updated_wip = await wip_crud.update_wip(db, wip_id, wip)
    if not updated_wip:
        raise HTTPException(status_code=404, detail="재공품을 찾을 수 없습니다.")
    return BaseResponse(status=200, message="재공품 상태/위치 업데이트 성공", data=updated_wip)