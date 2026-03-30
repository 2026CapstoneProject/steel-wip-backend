from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app import schemas, crud
from app.database import get_db

router = APIRouter(prefix="/wips", tags=["Steel WIPs"])

##응애애애애애
# 1. 잔재 등록
@router.post("/", response_model=schemas.BaseResponse[schemas.SteelWipResponse], status_code=status.HTTP_201_CREATED)
async def create_wip(wip: schemas.SteelWipCreate, db: AsyncSession = Depends(get_db)):
    db_wip = await crud.wip.create(db=db, wip=wip)
    return schemas.BaseResponse(status=201, message="잔재 등록에 성공했습니다.", data=db_wip)


# 2. 전체 잔재 조회
@router.get("/", response_model=schemas.BaseResponse[List[schemas.SteelWipResponse]])
async def read_wips(db: AsyncSession = Depends(get_db)):
    wips = await crud.wip.get_all(db=db)
    return schemas.BaseResponse(status=200, message="잔재 목록 조회에 성공했습니다.", data=wips)


# 3. 특정 잔재 조회
@router.get("/{wip_id}", response_model=schemas.BaseResponse[schemas.SteelWipResponse])
async def read_wip(wip_id: int, db: AsyncSession = Depends(get_db)):
    db_wip = await crud.wip.get(db, wip_id=wip_id)
    if db_wip is None:
        raise HTTPException(status_code=404, detail="잔재를 찾을 수 없습니다.")
    return schemas.BaseResponse(status=200, message="잔재 조회에 성공했습니다.", data=db_wip)


# 4. 잔재 정보 수정
@router.patch("/{wip_id}", response_model=schemas.BaseResponse[schemas.SteelWipResponse])
async def update_wip(wip_id: int, wip_update: schemas.SteelWipUpdate, db: AsyncSession = Depends(get_db)):
    db_wip = await crud.wip.update(db, wip_id=wip_id, wip_update=wip_update)
    if db_wip is None:
        raise HTTPException(status_code=404, detail="수정할 잔재를 찾을 수 없습니다.")
    return schemas.BaseResponse(status=200, message="잔재 정보 수정에 성공했습니다.", data=db_wip)


# 5. 잔재 삭제
@router.delete("/{wip_id}", response_model=schemas.BaseResponse[None])
async def delete_wip(wip_id: int, db: AsyncSession = Depends(get_db)):
    db_wip = await crud.wip.delete(db, wip_id=wip_id)
    if db_wip is None:
        raise HTTPException(status_code=404, detail="삭제할 잔재를 찾을 수 없습니다.")
    return schemas.BaseResponse(status=200, message="잔재 삭제에 성공했습니다.", data=None)
