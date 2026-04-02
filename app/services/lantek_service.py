# app/services/lantek_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime

from app.models import Projects, Scenarios, LazerCutting, EstimatedWips, QrCodes
from app.schemas.lantek import (
    LantekScenarioData, LantekCutting, LantekInput, LantekEstimatedWip
)

# app/services/lantek_service.py 의 POST 더미 데이터 생성 함수 복구
async def create_dummy_lantek_data(db: AsyncSession, scenario_id: int):
    # 시나리오는 이미 존재하므로 새로 만들지 않음
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")
        
    scenario.status = "DRAFT" # 상태 업데이트
    
    # 2. 더미 LazerCutting 데이터 생성
    dummy_cutting = LazerCutting(
        scenario_id=scenario_id,
        status="PENDING",
        priority="LOW"
    )
    db.add(dummy_cutting)
    await db.flush() 
    
    # 3. 더미 QrCodes 및 EstimatedWips 데이터 생성
    dummy_qr = QrCodes(qr_code=f"QR-DUMMY-{dummy_cutting.id}")
    db.add(dummy_qr)
    await db.flush()

    dummy_wip = EstimatedWips(
        lazer_cutting_id=dummy_cutting.id,
        manufacturer="POSCO",
        material="SM355A",
        thickness=6.0,
        width=600.0,
        length=1200.0, 
        weight=50.0,
        qr_id=dummy_qr.id
    )
    db.add(dummy_wip)
    await db.commit()

async def delete_lantek_data(db: AsyncSession, scenario_id: int):
    """DELETE: 시나리오에 종속된 커팅 및 예상 잔재 데이터를 모두 초기화"""
    # 1. 시나리오에 속한 커팅 ID 목록 조회
    cuttings = await db.execute(select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id))
    cutting_ids = [c[0] for c in cuttings.all()]
    
    if not cutting_ids:
        return
        
    # 2. 해당 커팅들의 예상 잔재(EstimatedWips) 조회하여 연결된 QR 코드 ID 확보
    wips = await db.execute(select(EstimatedWips.qr_id).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids)))
    qr_ids = [w[0] for w in wips.all() if w[0] is not None]
    
    # 3. 역순으로 삭제 (자식 데이터부터)
    await db.execute(delete(EstimatedWips).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids)))
    if qr_ids:
        await db.execute(delete(QrCodes).where(QrCodes.id.in_(qr_ids)))
    await db.execute(delete(LazerCutting).where(LazerCutting.scenario_id == scenario_id))
    
    await db.commit()

async def get_lantek_data(db: AsyncSession, scenario_id: int) -> list:
    """GET: 시나리오 결과 확인용 복합 데이터 조회 및 스키마 매핑"""
    # 1. 시나리오 및 연관된 프로젝트 정보 조회
    stmt = select(Scenarios, Projects).join(Projects, Scenarios.project_id == Projects.id).where(Scenarios.id == scenario_id)
    result = await db.execute(stmt)
    row = result.first()
    
    if not row:
        return []
        
    scenario, project = row
    
    # 2. 시나리오에 속한 절단 지시(LazerCutting) 및 예상 잔재(EstimatedWips) 조회
    cuttings_stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario.id)
    cuttings_result = await db.execute(cuttings_stmt)
    cuttings = cuttings_result.scalars().all()
    
    lazer_cutting_list = []
    for cut in cuttings:
        # 커팅별 예상 잔재 조회
        wips_stmt = select(EstimatedWips).where(EstimatedWips.lazer_cutting_id == cut.id)
        wips_result = await db.execute(wips_stmt)
        wips = wips_result.scalars().all()
        
        # 잔재 데이터 매핑
        estimated_wips_mapped = [
            LantekEstimatedWip(
                id=w.id,
                thickness=w.thickness or 0,
                width=w.width or 0,
                height=w.length or 0  # DB의 length를 JSON의 height로 변환
            ) for w in wips
        ]
        
        # 커팅 데이터 매핑 (input 부분은 현재 원본 데이터가 없으므로 더미 응답 규격에 맞춤)
        lazer_cutting_list.append(LantekCutting(
            id=cut.id,
            estimatedCuttingTime="03:00", # 더미 텍스트
            input=LantekInput(
                manufacturer="POSCO",
                material="SM355A",
                thickness=6.0,
                width=1024.0,
                height=6096.0
            ),
            estimatedWips=estimated_wips_mapped
        ))

    # 3. 최종 JSON 트리 구조 생성
    scenario_data = LantekScenarioData(
        projectId=project.id,
        projectTitle=project.title,
        projectDue=project.project_due,
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        scenarioDue=scenario.scenario_due,
        lazerName=scenario.lazer_name or "LAZER1",
        emergencyOrNot=scenario.emergency_or_not,
        lazerCutting=lazer_cutting_list
    )
    
    return [scenario_data] # 최상단을 리스트로 감싸서 반환