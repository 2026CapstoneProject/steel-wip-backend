# app/routers/wips.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.crud import wip as wip_crud
from app.schemas import SteelWipCreate, SteelWipUpdate, SteelWipResponse, BaseResponse

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

@router.get("", response_model=BaseResponse[List[SteelWipResponse]])
async def list_steel_wips(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    wips = await wip_crud.get_wips(db, skip, limit)
    return BaseResponse(status=200, message="재공품 목록 조회 성공", data=wips)

@router.patch("/{wip_id}", response_model=BaseResponse[SteelWipResponse])
async def update_steel_wip(wip_id: int, wip: SteelWipUpdate, db: AsyncSession = Depends(get_db)):
    updated_wip = await wip_crud.update_wip(db, wip_id, wip)
    if not updated_wip:
        raise HTTPException(status_code=404, detail="재공품을 찾을 수 없습니다.")
    return BaseResponse(status=200, message="재공품 상태/위치 업데이트 성공", data=updated_wip)