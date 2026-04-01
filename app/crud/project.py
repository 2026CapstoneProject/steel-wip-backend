# app/crud/project.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

# 수정됨: Project 대신 Projects를 임포트합니다.
from app.models import Projects
from app.schemas.project import ProjectCreate, ProjectUpdate

async def get_projects_by_search(db: AsyncSession, search: Optional[str] = None) -> List[Projects]:
    """검색어가 포함된 프로젝트 목록 조회 (대소문자 무시)"""
    stmt = select(Projects)
    if search:
        # ilike()를 사용하면 '토네이도' 검색 시 해당 단어가 포함된 모든 결과를 가져옴
        stmt = stmt.where(Projects.title.ilike(f"%{search}%"))
    
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def create_project(db: AsyncSession, project_in: ProjectCreate) -> Projects:
    """새 프로젝트 생성"""
    db_project = Projects(**project_in.model_dump())
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project

async def get_project_by_id(db: AsyncSession, project_id: int) -> Optional[Projects]:
    """ID로 단일 프로젝트 조회"""
    result = await db.execute(select(Projects).filter(Projects.id == project_id))
    return result.scalars().first()

async def update_project(db: AsyncSession, project_id: int, project_in: ProjectUpdate) -> Optional[Projects]:
    """프로젝트 수정"""
    db_project = await get_project_by_id(db, project_id)
    if not db_project:
        return None
        
    update_data = project_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_project, key, value)
        
    await db.commit()
    await db.refresh(db_project)
    return db_project