# app/services/lantek_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime
import random

from app.models import Projects, Scenarios, LazerCutting, EstimatedWips, QrCodes, SteelWip
from app.schemas.lantek import (
    LantekScenarioData, LantekCutting, LantekInput, LantekEstimatedWip
)
from app.schemas.enums import WipStatus


async def create_dummy_lantek_data(db: AsyncSession, scenario_id: int):
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")
        
    # [추가됨] 시나리오 상태를 None에서 DRAFT로 변경 (이제 파일이 업로드되었으므로 껍데기가 채워짐)
    scenario.status = "DRAFT"
    
    # 1. 원본 SteelWip 테이블에서 상태가 IN_STOCK인 철판만 랜덤으로 가져오기 위해 조회
    stmt = select(SteelWip).where(SteelWip.status == WipStatus.IN_STOCK.value).limit(50)
    wip_result = await db.execute(stmt)
    real_wips = wip_result.scalars().all()
    
    if not real_wips:
        raise ValueError("가용 가능한 재고(IN_STOCK)가 존재하지 않습니다.")

    # MAIN SOLVER 요구사항: 1 시나리오 당 총 12개의 커팅 (3배치 * 4커팅) 생성
    TOTAL_CUTTINGS = 12
    
    for _ in range(TOTAL_CUTTINGS):
        # 자를 원본 철판 무작위 선택
        target_wip = random.choice(real_wips)
        
        # [방어 로직 추가] 만약 선택된 철판이 IN_STOCK이 아니라면 (실제 파일 업로드 시나리오 대비)
        if target_wip.status != WipStatus.IN_STOCK.value:
            # 트랜잭션을 명시적으로 롤백하고 예외를 던짐
            await db.rollback()
            raise ValueError(f"WIP ID {target_wip.id}는 이미 할당된 재고입니다.")
        
        # 2. 커팅 지시(LazerCutting) 생성
        # 예상 커팅 시간은 15분 ~ 120분 사이 무작위 (Integer)
        cutting_time = random.randint(15, 120)
        
        dummy_cutting = LazerCutting(
            scenario_id=scenario_id,
            status="PENDING",
            priority=random.choice(["LOW", "MIDDLE", "HIGH"]),
            estimated_cutting_time=cutting_time,
            steel_wip_id=target_wip.id  # 어떤 철판을 자를 것인지 연결
        )
        db.add(dummy_cutting)
        await db.flush() # id 발급
        
        # 3. 잔재(EstimatedWips) 생성 (0개, 1개, 2개 중 무작위)
        num_wips = random.choice([0, 1, 2])
        
        for _ in range(num_wips):
            # 잔재는 원본 철판보다 작아야 하므로 크기를 줄임
            new_width = round(target_wip.width * random.uniform(0.3, 0.7), 1)
            new_length = round(target_wip.length * random.uniform(0.3, 0.7), 1)
            # 철의 비중(7.85)을 고려한 대략적 무게 계산 (kg)
            new_weight = round(target_wip.thickness * new_width * new_length * 7.85 / 1000000, 1)
            
            # QR 코드 발급
            dummy_qr = QrCodes(qr_code=f"QR-DUMMY-{dummy_cutting.id}-{random.randint(1000,9999)}")
            db.add(dummy_qr)
            await db.flush()
            
            dummy_wip = EstimatedWips(
                lazer_cutting_id=dummy_cutting.id,
                manufacturer=target_wip.manufacturer or "POSCO",
                material=target_wip.material,
                thickness=target_wip.thickness,
                width=new_width,
                length=new_length, 
                weight=new_weight,
                qr_id=dummy_qr.id
            )
            db.add(dummy_wip)
            
    await db.commit()

# app/services/lantek_service.py 파일 내부의 get_lantek_data 함수 교체

async def get_lantek_data(db: AsyncSession, scenario_id: int) -> list:
    """GET: 시나리오 결과 확인용 복합 데이터 조회 및 스키마 매핑"""
    # 1. 시나리오 및 연관된 프로젝트 정보 조회
    stmt = select(Scenarios, Projects).join(Projects, Scenarios.project_id == Projects.id).where(Scenarios.id == scenario_id)
    result = await db.execute(stmt)
    row = result.first()
    
    if not row:
        return []
        
    scenario, project = row
    
    # 2. 시나리오에 속한 절단 지시(LazerCutting) 조회
    cuttings_stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario.id)
    cuttings_result = await db.execute(cuttings_stmt)
    cuttings = cuttings_result.scalars().all()
    
    lazer_cutting_list = []
    for cut in cuttings:
        # 3. 각 커팅별 예상 잔재(EstimatedWips) 조회
        wips_stmt = select(EstimatedWips).where(EstimatedWips.lazer_cutting_id == cut.id)
        wips_result = await db.execute(wips_stmt)
        wips = wips_result.scalars().all()
        
        # 잔재 데이터를 Pydantic 스키마(LantekEstimatedWip) 형식에 맞게 리스트로 변환
        estimated_wips_mapped = [
            LantekEstimatedWip(
                id=w.id,
                thickness=w.thickness or 0.0,
                width=w.width or 0.0,
                height=w.length or 0.0,  # DB의 length 컬럼을 JSON 응답의 height 키로 변환
                weight=w.weight          # 절단 후 무게 (kg), 없으면 None
            ) for w in wips
        ]
        
        # 4. 분(Integer)으로 저장된 커팅 시간을 "HH:MM" 형식의 문자열로 변환
        total_minutes = cut.estimated_cutting_time or 0
        hours = total_minutes // 60
        mins = total_minutes % 60
        time_str = f"{hours:02d}:{mins:02d}"
        
        # 5. 해당 커팅 지시에 연결된 원본 철판(SteelWip) 정보 가져오기
        #    (연결된 철판이 없을 경우를 대비해 예외 처리 포함)
        source_wip = None
        if cut.steel_wip_id:
            source_wip = await db.get(SteelWip, cut.steel_wip_id)
        
        # 6. 최종적으로 하나의 커팅 지시 묶음(LantekCutting)을 생성하여 리스트에 추가
        lazer_cutting_list.append(LantekCutting(
            id=cut.id,
            estimatedCuttingTime=time_str, 
            input=LantekInput(
                manufacturer=source_wip.manufacturer if source_wip else "POSCO",
                material=source_wip.material if source_wip else "SM355A",
                thickness=source_wip.thickness if source_wip else 0.0,
                width=source_wip.width if source_wip else 0.0,
                height=source_wip.length if source_wip else 0.0 # 프론트 요청 규격(height)
            ),
            estimatedWips=estimated_wips_mapped
        ))

    # 7. 최종 JSON 트리 구조(LantekScenarioData) 조립
    scenario_data = LantekScenarioData(
        projectId=project.id,
        projectTitle=project.title,
        projectDue=project.project_due,
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        scenarioDue=scenario.scenario_due,
        lazerName=(scenario.lazer_name.value if hasattr(scenario.lazer_name, 'value') else (scenario.lazer_name or "LAZER1")),  # SQLite str / MySQL Enum 호환
        emergencyOrNot=scenario.emergency_or_not,
        lazerCutting=lazer_cutting_list
    )
    
    return [scenario_data] # 명세서 형식상 배열([])로 감싸서 반환해야 함


async def delete_lantek_data(db: AsyncSession, scenario_id: int) -> None:
    """
    DELETE: 시나리오의 LANTEK 데이터 초기화
    - LazerCutting, EstimatedWips, 관련 QrCodes 삭제
    - 시나리오 status → None으로 초기화 (LANTEK 재업로드 가능하도록)
    """
    # 1. 해당 시나리오의 LazerCutting ID 수집
    cutting_ids_stmt = select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id)
    cutting_ids = (await db.execute(cutting_ids_stmt)).scalars().all()

    if cutting_ids:
        # 2. 연결된 EstimatedWips 의 qr_id 수집
        qr_ids_stmt = select(EstimatedWips.qr_id).where(
            EstimatedWips.lazer_cutting_id.in_(cutting_ids)
        )
        qr_ids = [q for q in (await db.execute(qr_ids_stmt)).scalars().all() if q]

        # 3. EstimatedWips 삭제
        await db.execute(
            delete(EstimatedWips).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids))
        )
        # 4. QrCodes 삭제 (더미 데이터 생성 시 만든 QR만 해당)
        if qr_ids:
            await db.execute(delete(QrCodes).where(QrCodes.id.in_(qr_ids)))
        # 5. LazerCutting 삭제
        await db.execute(
            delete(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
        )

    # 6. 시나리오 상태 초기화 (LANTEK 재업로드 가능하도록 None으로 복원)
    scenario = await db.get(Scenarios, scenario_id)
    if scenario:
        scenario.status = None

    await db.commit()