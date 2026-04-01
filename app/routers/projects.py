# app/routers/projects.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.crud import project as project_crud
from app.schemas import BaseResponse
from app.schemas.project import (
    ProjectCreate, 
    ProjectUpdate, 
    ProjectResponse, 
    ProjectSearchData
)

router = APIRouter()

# 1. 프로젝트 검색 (GET)
@router.get("", response_model=BaseResponse[ProjectSearchData])
async def search_projects(
    search: Optional[str] = Query(None, description="프로젝트명 검색어"),
    db: AsyncSession = Depends(get_db)
):
    projects = await project_crud.get_projects_by_search(db, search)
    data = ProjectSearchData(search=search, projects=projects)
    return BaseResponse(
        status=200, 
        message="검색 결과 조회에 성공했습니다.", 
        data=data
    )

# 2. 프로젝트 신규 생성 (POST)
@router.post("/new", response_model=BaseResponse[ProjectResponse])
async def create_project(
    project_in: ProjectCreate, 
    db: AsyncSession = Depends(get_db)
):
    new_project = await project_crud.create_project(db, project_in)
    return BaseResponse(
        status=201, 
        message="프로젝트가 성공적으로 생성되었습니다.", 
        data=new_project
    )

# 3. 프로젝트 검색 결과 수정 (PATCH)
@router.patch("/{project_id}", response_model=BaseResponse[ProjectResponse])
async def update_project(
    project_id: int, 
    project_in: ProjectUpdate, 
    db: AsyncSession = Depends(get_db)
):
    updated_project = await project_crud.update_project(db, project_id, project_in)
    if not updated_project:
        raise HTTPException(status_code=404, detail="해당 프로젝트를 찾을 수 없습니다.")
        
    return BaseResponse(
        status=201, 
        message="프로젝트가 성공적으로 수정되었습니다.", 
        data=updated_project
    )

# 4. 프로젝트 선택 (POST)
@router.post("/{project_id}", response_model=BaseResponse[ProjectResponse])
async def select_project(
    project_id: int, 
    db: AsyncSession = Depends(get_db)
):
    project = await project_crud.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="해당 프로젝트를 찾을 수 없습니다.")
        
    # 향후 이 위치에 트랜잭션, 세션 저장, 상태 변경 등 추가 로직 구현 가능
    
    return BaseResponse(
        status=201, 
        message="프로젝트가 성공적으로 선택되었습니다.", 
        data=project
    )