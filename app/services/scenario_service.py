# app/services/scenario_service.py 생성
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, datetime, timedelta
from typing import Optional

from app.models import Projects, Scenarios

# app/services/scenario_service.py
from sqlalchemy import func, update, delete
from sqlalchemy.orm import selectinload
from app.models import (
    Projects, Scenarios, LazerCutting, Batch, BatchItems, 
    SteelWip, Locations, EstimatedWips, QrCodes
)
from app.schemas.scenario import ScenarioResultData, BatchItemDetail
from app.schemas.enums import BatchActionType

from app.schemas.scenario import ScenarioHistoryItem, ProjectScenarioHistory, SentScenarioItem, SentProjectHistory
from app.schemas.batch_item import BatchItemStatus

async def get_or_create_scenario(db: AsyncSession, project_id: int, scenario_due: date) -> Scenarios:
    """
    POST: 생산계획명 생성 로직 (수정됨)
    - 동일한 프로젝트 + due를 가진 시나리오 중 status가 None인 것이 있다면 재사용
    - 만약 status가 None이 아닌 것만 존재한다면, 가장 최근의 시나리오 title을 그대로 복사하여 
      status=None 인 새로운 시나리오(비교군)를 생성
    """
    
    # 1. 동일한 프로젝트 + 동일한 due를 가진 시나리오들을 최신순으로 모두 조회
    stmt_existing = select(Scenarios).where(
        Scenarios.project_id == project_id,
        Scenarios.scenario_due == scenario_due
    ).order_by(Scenarios.id.desc())
    
    result = await db.execute(stmt_existing)
    existing_scenarios = result.scalars().all()
    
    # 2. 조회된 시나리오 중 status가 None인 시나리오가 있는지 확인 (가장 최신 것 1개)
    #    (None이면 아직 진행되지 않은 껍데기이므로 그대로 재사용)
    for scenario in existing_scenarios:
        if scenario.status is None:
            return scenario
            
    # 3. 만약 모두 status가 None이 아니라면(이미 진행 중이라면)
    #    혹은 아예 일치하는 시나리오가 없다면 새로 생성해야 함.
    
    project = await db.get(Projects, project_id)
    if not project:
        raise ValueError("해당 프로젝트를 찾을 수 없습니다.")
        
    # 새 타이틀 결정 로직
    new_title = ""
    if existing_scenarios:
        # 기존 시나리오가 있다면, 동일한 title(가장 최신 것 기준)을 그대로 복사
        new_title = existing_scenarios[0].title
    else:
        # 아예 처음 만드는 due 라면 새로 넘버링(N+1)하여 title 생성
        # (이 프로젝트에 속한 '고유한 title'의 개수를 세어 N+1을 붙임)
        stmt_count = select(func.count(func.distinct(Scenarios.title))).where(
            Scenarios.project_id == project_id
        )
        count_result = await db.execute(stmt_count)
        unique_title_count = count_result.scalar() or 0
        new_title = f"{project.title}-{unique_title_count + 1}"
    
    # 4. 새 시나리오 생성 (비교군 또는 신규)
    # status는 명시하지 않거나 None으로 두어 DB default(None)가 되도록 함.
    new_scenario = Scenarios(
        title=new_title,
        scenario_order=0,
        status=None,  # 수정됨: DRAFT 대신 None으로 초기화
        created_at=datetime.now(),
        scenario_due=scenario_due,
        lazer_name="LAZER1",
        emergency_or_not=False,
        project_id=project_id,
        creator_id=1,   
        assignee_id=2   
    )
    
    db.add(new_scenario)
    await db.commit()
    await db.refresh(new_scenario)
    
    return new_scenario



async def get_scenario_result(db: AsyncSession, scenario_id: int) -> list:
    """GET: 시나리오 결과 및 배치 통계 조회"""
    # 1. 시나리오 및 프로젝트 정보
    stmt = select(Scenarios, Projects).join(Projects).where(Scenarios.id == scenario_id)
    row = (await db.execute(stmt)).first()
    if not row:
        return []
    scenario, project = row

    # 2. 총 절단 시간 계산 (LazerCutting)
    cutting_stmt = select(func.sum(LazerCutting.estimated_cutting_time)).where(LazerCutting.scenario_id == scenario_id)
    total_cutting_time = (await db.execute(cutting_stmt)).scalar() or 0

    # 3. Batch 및 BatchItems 조회 (WIP, Location 정보 포함)
    batch_stmt = select(Batch).where(Batch.scenario_id == scenario_id)
    batches = (await db.execute(batch_stmt)).scalars().all()
    batch_ids = [b.id for b in batches]

    batch_items = []
    total_wip_num = 0
    total_move_num = 0

    if batch_ids:
        items_stmt = select(BatchItems).where(BatchItems.batch_id.in_(batch_ids)).order_by(BatchItems.expected_start_time)
        items_result = (await db.execute(items_stmt)).scalars().all()

        for item in items_result:
            total_move_num += 1
            if item.batch_item_action == BatchActionType.PICKING.value:
                total_wip_num += 1

            # WIP 정보
            wip = await db.get(SteelWip, item.steel_wip_id) if item.steel_wip_id else None
            
            # Location 명칭 치환
            from_loc = await db.get(Locations, item.from_location) if item.from_location else None
            to_loc = await db.get(Locations, item.to_location) if item.to_location else None

            # Action 이름 한글 매핑
            action_name = "재배치" if item.batch_item_action == "RELOCATE" else "피킹" if item.batch_item_action == "PICKING" else "적재"

            batch_items.append(BatchItemDetail(
                batchItemAction=action_name,
                steelWipId=wip.id if wip else 0,
                manufacturer=wip.manufacturer if wip else "알수없음",
                material=wip.material if wip else "알수없음",
                thickness=wip.thickness if wip else 0.0,
                width=wip.width if wip else 0.0,
                length=wip.length if wip else 0.0,
                weight=wip.weight if wip else 0.0,
                fromLocation=from_loc.loc_name if from_loc else None,
                toLocation=to_loc.loc_name if to_loc else None,
                expectedStartTime=item.expected_start_time
            ))

    # 더미 계산: 재배치 후 피킹 크레인 교체 이동 횟수 (단순화: 총 이동 횟수 * 1.5 등 로직에 맞게 조정 가능)
    total_crane_move = total_move_num + total_wip_num 

    result_data = ScenarioResultData(
        projectId=project.id,
        projectTitle=project.title,
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        scenarioDue=scenario.scenario_due,
        lazerName=scenario.lazer_name.value if scenario.lazer_name else "LAZER1",
        totalCuttingTime=total_cutting_time,
        totalWipNum=total_wip_num,
        totalCraneMove=total_crane_move,
        totalMoveNum=total_move_num,
        batchItems=batch_items
    )
    
    return [result_data]

async def publish_scenario(db: AsyncSession, scenario_id: int):
    """POST: 시나리오 발행 (상태값 변경 트랜잭션)"""
    # 1. 시나리오 상태 변경 (DRAFT -> ORDERED)
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")
    scenario.status = "ORDERED"

    # 2. Batch 조회
    batches = (await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))).scalars().all()
    if not batches:
        await db.commit()
        return

    # 3. BatchItems (PICKING인 것들만) 상태 변경
    items_stmt = select(BatchItems).where(
        BatchItems.batch_id.in_(batches),
        BatchItems.batch_item_action == BatchActionType.PICKING.value
    )
    picking_items = (await db.execute(items_stmt)).scalars().all()
    wip_ids = []

    for item in picking_items:
        item.status = "PENDING"
        if item.steel_wip_id:
            wip_ids.append(item.steel_wip_id)

    # 4. 연결된 SteelWip 상태 변경 (IN_STOCK -> RESERVATED)
    if wip_ids:
        await db.execute(
            update(SteelWip)
            .where(SteelWip.id.in_(wip_ids))
            .values(status="RESERVATED")
        )

    await db.commit()

async def delete_scenario_cascade(db: AsyncSession, scenario_id: int):
    """DELETE: 시나리오 및 종속된 모든 데이터 삭제"""
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")

    # 1. LazerCutting, EstimatedWips, QrCodes 삭제
    cuttings = (await db.execute(select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id))).scalars().all()
    if cuttings:
        wips = (await db.execute(select(EstimatedWips.qr_id).where(EstimatedWips.lazer_cutting_id.in_(cuttings)))).scalars().all()
        qr_ids = [q for q in wips if q]
        
        await db.execute(delete(EstimatedWips).where(EstimatedWips.lazer_cutting_id.in_(cuttings)))
        if qr_ids:
            await db.execute(delete(QrCodes).where(QrCodes.id.in_(qr_ids)))
        await db.execute(delete(LazerCutting).where(LazerCutting.scenario_id == scenario_id))

    # 2. Batch, BatchItems 삭제
    batches = (await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))).scalars().all()
    if batches:
        await db.execute(delete(BatchItems).where(BatchItems.batch_id.in_(batches)))
        await db.execute(delete(Batch).where(Batch.scenario_id == scenario_id))

    # 3. 최상위 시나리오 삭제
    await db.delete(scenario)
    await db.commit()


async def get_scenario_history(
    db: AsyncSession,
    project_name: Optional[str] = None,
    scenario_name: Optional[str] = None,
    proj_due_min: Optional[date] = None,
    proj_due_max: Optional[date] = None,
    scen_due_min: Optional[date] = None,
    scen_due_max: Optional[date] = None,
    gen_date_min: Optional[date] = None,
    gen_date_max: Optional[date] = None
) -> list:
    """GET: 시나리오 생성 이력 다중 필터링 및 통계 조회 (DRAFT 상태만)"""
    
    # 1. 기본 조인 쿼리 (Scenarios + Projects)
    # 여기에 Scenarios.status == "DRAFT" 조건을 기본으로 추가합니다.
    stmt = (
        select(Scenarios, Projects)
        .join(Projects, Scenarios.project_id == Projects.id)
        .where(Scenarios.status == "DRAFT")  # <-- DRAFT 상태 필터링 추가
    )
    
    # 2. 동적 필터링 적용 (이하 로직은 기존과 완전히 동일합니다)
    if project_name:
        stmt = stmt.where(Projects.title.ilike(f"%{project_name}%"))
    if scenario_name:
        stmt = stmt.where(Scenarios.title.ilike(f"%{scenario_name}%"))
        
    if proj_due_min:
        stmt = stmt.where(Projects.project_due >= proj_due_min)
    if proj_due_max:
        stmt = stmt.where(Projects.project_due <= proj_due_max)
        
    if scen_due_min:
        stmt = stmt.where(Scenarios.scenario_due >= scen_due_min)
    if scen_due_max:
        stmt = stmt.where(Scenarios.scenario_due <= scen_due_max)
        
    # gen_date는 datetime(created_at)이므로, max값은 해당 일자의 23:59:59까지 포함하도록 처리
    if gen_date_min:
        stmt = stmt.where(Scenarios.created_at >= datetime.combine(gen_date_min, datetime.min.time()))
    if gen_date_max:
        stmt = stmt.where(Scenarios.created_at <= datetime.combine(gen_date_max, datetime.max.time()))

    result = await db.execute(stmt)
    rows = result.all()

    # 3. 프로젝트 기준으로 데이터 그룹화 및 통계 계산
    projects_map = {}
    
    for scenario, project in rows:
        if project.id not in projects_map:
            projects_map[project.id] = {
                "projectId": project.id,
                "projectTitle": project.title,
                "scenario": []
            }
            
        # [통계 1] 총 예상 커팅 시간
        cut_stmt = select(func.sum(LazerCutting.estimated_cutting_time)).where(LazerCutting.scenario_id == scenario.id)
        total_minute = (await db.execute(cut_stmt)).scalar() or 0
        
        # [통계 2] 배치 아이템(피킹, 재배치) 개수 카운트
        batch_stmt = select(BatchItems.batch_item_action).join(Batch, BatchItems.batch_id == Batch.id).where(Batch.scenario_id == scenario.id)
        actions = (await db.execute(batch_stmt)).scalars().all()
        
        selected_wips = sum(1 for a in actions if a == BatchActionType.PICKING.value)
        num_relocation = sum(1 for a in actions if a == BatchActionType.RELOCATE.value)
        
        # [통계 3] 크레인 이동 횟수 (임의 로직: 피킹 횟수 + 재배치 횟수 + 기본 이동값 등 조정 가능)
        num_crane = selected_wips + num_relocation
        
        # 시나리오 데이터 조립
        scenario_item = ScenarioHistoryItem(
            id=scenario.id,
            title=scenario.title,
            due=scenario.scenario_due,
            lazerName=scenario.lazer_name.value if scenario.lazer_name else "LAZER1",
            selectedWips=selected_wips,
            num_relocation=num_relocation, # Pydantic이 출력 시 "#relocation"으로 자동 치환
            num_crane=num_crane,           # Pydantic이 출력 시 "#crane"으로 자동 치환
            totalMinute=total_minute
        )
        
        projects_map[project.id]["scenario"].append(scenario_item)

    # 4. Dictionary의 값들만 추출해서 List 형태로 반환
    return [ProjectScenarioHistory(**data) for data in projects_map.values()]


# app/services/scenario_service.py 하단에 추가
async def send_scenario_to_field(db: AsyncSession, scenario_id: int):
    """
    POST: 시나리오 현장 전송
    1. 선택된 시나리오 상태 ORDERED 변경 및 ordered_at 기록
    2. 연관된 BatchItems(PICKING) 상태 PENDING 변경
    3. 연관된 SteelWip 상태 RESERVATED 변경
    4. 동일한 title을 가졌지만 선택되지 않은 다른 DRAFT 시나리오 삭제
    """
    # 1. 대상 시나리오 조회 및 유효성 검사
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("전송할 시나리오를 찾을 수 없습니다.")
        
    if scenario.status != "DRAFT":
        raise ValueError("대기(DRAFT) 상태인 시나리오만 전송할 수 있습니다.")

    target_title = scenario.title
    
    # 2. 선택된 시나리오 상태 변경
    scenario.status = "ORDERED"
    scenario.ordered_at = datetime.now()
    
    # 3. Batch 조회
    batch_stmt = select(Batch.id).where(Batch.scenario_id == scenario_id)
    batches = (await db.execute(batch_stmt)).scalars().all()
    
    if batches:
        # 4. BatchItems 상태 PENDING 변경 (PICKING인 것들만)
        items_stmt = select(BatchItems).where(
            BatchItems.batch_id.in_(batches),
            BatchItems.batch_item_action == BatchActionType.PICKING.value
        )
        picking_items = (await db.execute(items_stmt)).scalars().all()
        
        wip_ids = []
        for item in picking_items:
            item.status = BatchItemStatus.PENDING.value
            if item.steel_wip_id:
                wip_ids.append(item.steel_wip_id)
                
        # 5. 연결된 SteelWip 상태 RESERVATED로 예약 변경
        if wip_ids:
            await db.execute(
                update(SteelWip)
                .where(SteelWip.id.in_(wip_ids))
                .values(status="RESERVATED")
            )
            
    # 6. 동일한 title을 가졌지만 선택받지 못한 다른 DRAFT 시나리오들 조회
    other_scenarios_stmt = select(Scenarios.id).where(
        Scenarios.title == target_title,
        Scenarios.id != scenario_id,
        Scenarios.status == "DRAFT"
    )
    other_scenario_ids = (await db.execute(other_scenarios_stmt)).scalars().all()
    
    # 7. 버려진 시나리오들 연쇄 삭제 (이전 삭제 로직 활용)
    for other_id in other_scenario_ids:
        # 이미 작성되어 있는 delete_scenario_cascade 함수를 재활용하여 하위 데이터까지 싹 지웁니다.
        await delete_scenario_cascade(db, other_id)
        
    await db.commit()

# app/services/scenario_service.py 하단에 추가

async def get_sent_scenario_history(
    db: AsyncSession,
    project_name: Optional[str] = None,
    proj_due_min: Optional[date] = None,
    proj_due_max: Optional[date] = None,
    scen_due_min: Optional[date] = None,
    scen_due_max: Optional[date] = None,
    send_date_min: Optional[date] = None,
    send_date_max: Optional[date] = None
) -> list:
    """GET: 현장에 전송된 시나리오 이력 다중 필터링 및 통계 조회"""
    
    # 1. 기본 조인 쿼리 (Scenarios + Projects)
    # DRAFT가 아닌(ORDERED, IN_PROGRESS, COMPLETED) 시나리오만 조회합니다.
    stmt = (
        select(Scenarios, Projects)
        .join(Projects, Scenarios.project_id == Projects.id)
        .where(Scenarios.status != "DRAFT")
    )
    
    # 2. 동적 필터링 적용
    if project_name:
        stmt = stmt.where(Projects.title.ilike(f"%{project_name}%"))
        
    if proj_due_min:
        stmt = stmt.where(Projects.project_due >= proj_due_min)
    if proj_due_max:
        stmt = stmt.where(Projects.project_due <= proj_due_max)
        
    if scen_due_min:
        stmt = stmt.where(Scenarios.scenario_due >= scen_due_min)
    if scen_due_max:
        stmt = stmt.where(Scenarios.scenario_due <= scen_due_max)
        
    # send_date는 datetime(ordered_at) 기준입니다.
    if send_date_min:
        stmt = stmt.where(Scenarios.ordered_at >= datetime.combine(send_date_min, datetime.min.time()))
    if send_date_max:
        stmt = stmt.where(Scenarios.ordered_at <= datetime.combine(send_date_max, datetime.max.time()))

    result = await db.execute(stmt)
    rows = result.all()

    # 3. 프로젝트 기준으로 데이터 그룹화 및 통계 계산
    projects_map = {}
    
    for scenario, project in rows:
        if project.id not in projects_map:
            projects_map[project.id] = {
                "projectId": project.id,
                "projectTitle": project.title,
                "projectDue": project.project_due,
                "scenarios": []
            }
            
        # [통계] 해당 시나리오에 포함된 PICKING 작업 개수 산출 (투입 WIPS 개수)
        batch_stmt = (
            select(func.count(BatchItems.id))
            .join(Batch, BatchItems.batch_id == Batch.id)
            .where(
                Batch.scenario_id == scenario.id,
                BatchItems.batch_item_action == BatchActionType.PICKING.value
            )
        )
        num_input_wip = (await db.execute(batch_stmt)).scalar() or 0
        
        # 시나리오 데이터 조립
        scenario_item = SentScenarioItem(
            scenarioId=scenario.id,
            scenarioTitle=scenario.title,
            scenarioDue=scenario.scenario_due,
            orderedAt=scenario.ordered_at or scenario.created_at, # 예외 방지용 fallback
            numInputWip=num_input_wip
        )
        
        projects_map[project.id]["scenarios"].append(scenario_item)

    # 4. Dictionary를 List 객체 형태로 반환
    return [SentProjectHistory(**data) for data in projects_map.values()]