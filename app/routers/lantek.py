# app/routers/lantek.py
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas import BaseResponse
from app.schemas.lantek import LantekScenarioData, LantekDeleteRequest
from app.services import lantek_service

router = APIRouter()


@router.get("/get/{scenario_id}", response_model=BaseResponse[List[LantekScenarioData]])
async def get_lantek_scenario(scenario_id: int, db: AsyncSession = Depends(get_db)):
    data = await lantek_service.get_lantek_data(db, scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오 정보 조회에 성공했습니다.",
        data=data
    )


@router.post("/import", response_model=BaseResponse[List[LantekScenarioData]])
async def import_lantek_pdf(
    scenario_id: int = Form(..., description="데이터를 연결할 시나리오 ID"),
    files: List[UploadFile] = File(...),  # 단일 → 복수
    db: AsyncSession = Depends(get_db)
):
    try:
        files_data = []
        for f in files:
            file_bytes = await f.read()
            files_data.append({"bytes": file_bytes, "filename": f.filename})

        await lantek_service.create_lantek_data_from_pdfs(
            db,
            scenario_id,
            files_data=files_data,
        )

        data = await lantek_service.get_lantek_data(db, scenario_id)
        return BaseResponse(
            status=201,
            message="LANTEK 결과 처리에 성공했습니다.",
            data=data
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/delete", response_model=BaseResponse)
async def delete_lantek(request: LantekDeleteRequest, db: AsyncSession = Depends(get_db)):
    await lantek_service.delete_lantek_data(db, request.scenario_id)
    return BaseResponse(
        status=200,
        message="시나리오 LANTEK 초기화가 완료되었습니다.",
        data=None
    )