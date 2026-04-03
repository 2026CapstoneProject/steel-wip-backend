# app/services/scenario_service.py 생성
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, datetime

from app.models import Projects, Scenarios

async def get_or_create_scenario(db: AsyncSession, project_id: int, scenario_due: date) -> Scenarios:
    # 1. 동일한 프로젝트 + 동일한 due를 가진 시나리오가 이미 있는지 확인
    stmt_existing = select(Scenarios).where(
        Scenarios.project_id == project_id,
        Scenarios.scenario_due == scenario_due
    )
    result = await db.execute(stmt_existing)
    existing_scenario = result.scalars().first()
    
    # 이미 존재한다면 새로 만들지 않고 그대로 반환
    if existing_scenario:
        return existing_scenario
        
    # 2. 존재하지 않는다면, 해당 프로젝트의 이름을 조회
    project = await db.get(Projects, project_id)
    if not project:
        raise ValueError("해당 프로젝트를 찾을 수 없습니다.")
        
    # 3. 해당 프로젝트에 종속된 기존 시나리오가 몇 개 있는지(N) 세어서 N+1 생성
    # title을 project_title-1, project_title-2 형식으로 맞추기 위함
    stmt_count = select(func.count(Scenarios.id)).where(Scenarios.project_id == project_id)
    count_result = await db.execute(stmt_count)
    scenario_count = count_result.scalar() or 0
    
    new_title = f"{project.title}-{scenario_count + 1}"
    
    # 4. 새 시나리오 생성 (creator_id=1, assignee_id=2 하드코딩 반영)
    new_scenario = Scenarios(
        title=new_title,
        scenario_order=0,
        status="DRAFT",
        created_at=datetime.now(),
        scenario_due=scenario_due,
        lazer_name="LAZER1",
        emergency_or_not=False,
        project_id=project_id,
        creator_id=1,   # 명세서 요청사항 반영
        assignee_id=2   # 명세서 요청사항 반영
    )
    
    db.add(new_scenario)
    await db.commit()
    await db.refresh(new_scenario)
    
    return new_scenario

