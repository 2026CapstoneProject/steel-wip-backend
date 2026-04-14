# app/seed.py
"""
데이터베이스 시드 데이터 초기화
백엔드 시작 시 호출되어 테이블을 초기화하고 샘플 데이터를 삽입합니다.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    Locations, QrCodes, Users, SteelWip, Projects, Scenarios, Batch,
    LazerCutting, EstimatedWips, BatchItems
)
from sqlalchemy import delete
from datetime import datetime, date


async def seed_database(db: AsyncSession) -> None:
    """데이터베이스를 초기화하고 샘플 데이터를 삽입합니다."""

    # ─────────────────────────────────────────────────────────
    # 1. 모든 테이블 데이터 삭제 (기존 데이터 제거)
    # ─────────────────────────────────────────────────────────
    await db.execute(delete(BatchItems))
    await db.execute(delete(EstimatedWips))
    await db.execute(delete(LazerCutting))
    await db.execute(delete(Batch))
    await db.execute(delete(Scenarios))
    await db.execute(delete(Projects))
    await db.execute(delete(SteelWip))
    await db.execute(delete(Users))
    await db.execute(delete(QrCodes))
    await db.execute(delete(Locations))

    # ─────────────────────────────────────────────────────────
    # 2. Locations (창고 구역)
    # ─────────────────────────────────────────────────────────
    locations = [
        Locations(id=1, loc_name='Zone A-1', loc_can_stock=1, loc_stack_height=3),
        Locations(id=2, loc_name='Zone A-2', loc_can_stock=1, loc_stack_height=3),
        Locations(id=3, loc_name='Zone A-3', loc_can_stock=1, loc_stack_height=3),
        Locations(id=4, loc_name='Zone B-1', loc_can_stock=1, loc_stack_height=3),
        Locations(id=5, loc_name='Zone B-2', loc_can_stock=1, loc_stack_height=3),
        Locations(id=6, loc_name='Zone B-3', loc_can_stock=1, loc_stack_height=3),
        Locations(id=7, loc_name='Zone C-1', loc_can_stock=1, loc_stack_height=3),
        Locations(id=8, loc_name='Zone C-2', loc_can_stock=1, loc_stack_height=3),
        Locations(id=9, loc_name='Zone C-3', loc_can_stock=1, loc_stack_height=3),
        Locations(id=10, loc_name='LAZER1', loc_can_stock=0, loc_stack_height=0),
        Locations(id=11, loc_name='LAZER2', loc_can_stock=0, loc_stack_height=0),
    ]
    db.add_all(locations)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 3. QR Codes
    # ─────────────────────────────────────────────────────────
    qr_codes = [
        QrCodes(id=1, qr_code='QR-WIP-001'),
        QrCodes(id=2, qr_code='QR-WIP-002'),
        QrCodes(id=3, qr_code='QR-WIP-003'),
        QrCodes(id=4, qr_code='QR-WIP-004'),
        QrCodes(id=5, qr_code='QR-WIP-005'),
        QrCodes(id=6, qr_code='QR-WIP-006'),
        QrCodes(id=7, qr_code='QR-WIP-007'),
        QrCodes(id=8, qr_code='QR-GEN-001'),
        QrCodes(id=9, qr_code='QR-GEN-002'),
    ]
    db.add_all(qr_codes)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 4. Users
    # ─────────────────────────────────────────────────────────
    users = [
        Users(id=1, username='김철수', department='생산관리팀', role='OFFICE', user_num=1001),
        Users(id=2, username='이현장', department='생산팀', role='FIELD', user_num=2001),
    ]
    db.add_all(users)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 5. Steel WIPs
    # ─────────────────────────────────────────────────────────
    steel_wips = [
        SteelWip(id=1, status='RESERVATED', material='SM420B', thickness=20, width=1000, length=2500, weight=392.7, manufacturer='현대제철', location_id=1, stack_level=1, qr_id=1),
        SteelWip(id=2, status='RESERVATED', material='GS400', thickness=12, width=800, length=1500, weight=113.0, manufacturer='현대제철', location_id=4, stack_level=1, qr_id=2),
        SteelWip(id=3, status='RESERVATED', material='SS275', thickness=20, width=1200, length=2500, weight=471.0, manufacturer='현대제철', location_id=8, stack_level=1, qr_id=3),
        SteelWip(id=4, status='IN_STOCK', material='SM355A', thickness=16, width=900, length=2200, weight=249.5, manufacturer='현대제철', location_id=2, stack_level=1, qr_id=4),
        SteelWip(id=5, status='RESERVATED', material='SM420B', thickness=12, width=1000, length=1500, weight=141.4, manufacturer='동국제강', location_id=9, stack_level=1, qr_id=5),
        SteelWip(id=6, status='IN_STOCK', material='SM420B', thickness=12, width=1000, length=1500, weight=141.4, manufacturer='동국제강', location_id=3, stack_level=1, qr_id=6),
        SteelWip(id=7, status='IN_STOCK', material='SS275', thickness=9, width=500, length=1000, weight=35.3, manufacturer='포스코', location_id=1, stack_level=2, qr_id=7),
        SteelWip(id=8, status='REGISTERED', material='GS400', thickness=12, width=400, length=1500, weight=56.5, manufacturer='현대제철', location_id=None, stack_level=None, qr_id=8),
        SteelWip(id=9, status='REGISTERED', material='SS275', thickness=20, width=600, length=2500, weight=235.5, manufacturer='현대제철', location_id=None, stack_level=None, qr_id=9),
    ]
    db.add_all(steel_wips)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 6. Projects
    # ─────────────────────────────────────────────────────────
    projects = [
        Projects(id=1, title='철강 가공 프로젝트 A', project_due=date(2026, 5, 30)),
        Projects(id=2, title='철강 가공 프로젝트 B', project_due=date(2026, 6, 15)),
    ]
    db.add_all(projects)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 7. Scenarios
    # ─────────────────────────────────────────────────────────
    scenarios = [
        Scenarios(
            id=1, title='시나리오 A-1 (진행 중)', status='IN_PROGRESS',
            scenario_due=date(2026, 4, 20), scenario_order=1, lazer_name='LAZER1',
            project_id=1, creator_id=1, assignee_id=2, emergency_or_not=0, created_at=datetime.now()
        ),
        Scenarios(
            id=2, title='시나리오 A-2 (대기)', status='ORDERED',
            scenario_due=date(2026, 4, 25), scenario_order=2, lazer_name='LAZER1',
            project_id=1, creator_id=1, assignee_id=2, emergency_or_not=0, created_at=datetime.now()
        ),
        Scenarios(
            id=3, title='시나리오 B-1 (초안)', status='DRAFT',
            scenario_due=date(2026, 5, 10), scenario_order=3, lazer_name='LAZER2',
            project_id=2, creator_id=1, assignee_id=2, emergency_or_not=0, created_at=datetime.now()
        ),
    ]
    db.add_all(scenarios)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 8. Batch
    # ─────────────────────────────────────────────────────────
    batches = [
        Batch(id=1, scenario_id=1, batch_order=1),
        Batch(id=2, scenario_id=1, batch_order=2),
    ]
    db.add_all(batches)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 9. LazerCutting
    # ─────────────────────────────────────────────────────────
    lazer_cuttings = [
        LazerCutting(id=1, scenario_id=1, batch_id=1, steel_wip_id=2, priority='HIGH', estimated_cutting_time=60, status='PENDING'),
        LazerCutting(id=2, scenario_id=1, batch_id=1, steel_wip_id=3, priority='MIDDLE', estimated_cutting_time=90, status='PENDING'),
    ]
    db.add_all(lazer_cuttings)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 10. EstimatedWips
    # ─────────────────────────────────────────────────────────
    estimated_wips = [
        EstimatedWips(id=1, lazer_cutting_id=1, qr_id=8, manufacturer='현대제철', material='GS400', thickness=12, width=400, length=1500, weight=56.5),
        EstimatedWips(id=2, lazer_cutting_id=2, qr_id=9, manufacturer='현대제철', material='SS275', thickness=20, width=600, length=2500, weight=235.5),
    ]
    db.add_all(estimated_wips)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 11. Batch Items
    # ─────────────────────────────────────────────────────────
    batch_items = [
        # Batch 1 (생산 중)
        BatchItems(id=1, batch_id=1, batch_item_action='PICKING', status='PENDING', steel_wip_id=2, batch_item_order=1, from_location=4, to_location=10, expected_start_time=0, expected_running_time=10),
        BatchItems(id=2, batch_id=1, batch_item_action='PICKING', status='PENDING', steel_wip_id=3, batch_item_order=2, from_location=8, to_location=10, expected_start_time=0, expected_running_time=10),
        BatchItems(id=3, batch_id=1, batch_item_action='INBOUND', status='PENDING', steel_wip_id=8, batch_item_order=3, from_location=10, to_location=6, expected_start_time=0, expected_running_time=10),
        BatchItems(id=4, batch_id=1, batch_item_action='INBOUND', status='PENDING', steel_wip_id=9, batch_item_order=4, from_location=10, to_location=7, expected_start_time=0, expected_running_time=10),
        # Batch 2 (생산 준비)
        BatchItems(id=5, batch_id=2, batch_item_action='RELOCATE', status='PENDING', steel_wip_id=4, batch_item_order=1, from_location=2, to_location=5, expected_start_time=0, expected_running_time=15),
        BatchItems(id=6, batch_id=2, batch_item_action='PICKING', status='PENDING', steel_wip_id=1, batch_item_order=2, from_location=1, to_location=10, expected_start_time=0, expected_running_time=10),
        BatchItems(id=7, batch_id=2, batch_item_action='PICKING', status='PENDING', steel_wip_id=5, batch_item_order=3, from_location=9, to_location=10, expected_start_time=0, expected_running_time=10),
    ]
    db.add_all(batch_items)
    await db.flush()

    # ─────────────────────────────────────────────────────────
    # 데이터 커밋
    # ─────────────────────────────────────────────────────────
    await db.commit()
    print("✅ 시드 데이터 초기화 완료!")
