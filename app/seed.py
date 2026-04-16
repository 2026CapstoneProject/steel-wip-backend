# app/seed.py
"""
데이터베이스 시드 데이터 초기화
백엔드 시작 시 호출되어 사용자 여정 데모용 기준 데이터를 삽입합니다.

- 시작 시점에는 시나리오/배치/작업지시가 하나도 없는 상태를 만든다.
- 생산계획자는 Office에서 LANTEK import → 시나리오 확인 → 발행을 수행한다.
- 작업자는 발행 이후에만 App(Field)에서 시나리오를 확인할 수 있다.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    Locations, QrCodes, Users, SteelWip, Projects, BatchItems,
    EstimatedWips, LazerCutting, Batch, Scenarios
)
from sqlalchemy import delete
from datetime import date
from app.services.demo_solver_service import (
    load_demo_seed_input_wips,
    load_demo_seed_output_wips,
)


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
    seed_input_wips = load_demo_seed_input_wips()
    seed_output_wips = load_demo_seed_output_wips()
    seed_all_wips = [*seed_input_wips, *seed_output_wips]
    qr_codes = [
        QrCodes(
            id=spec.id,
            qr_code=(f'QR-WIP-{spec.id:03d}' if spec.status == 'IN_STOCK' else f'DEMO-WIP-{spec.id}')
        )
        for spec in seed_all_wips
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
        SteelWip(
            id=spec.id,
            status=spec.status,
            material=spec.material,
            thickness=spec.thickness,
            width=spec.width,
            length=spec.length,
            weight=round(spec.thickness * spec.width * spec.length * (7.85 / 1_000_000), 1),
            manufacturer=spec.manufacturer,
            location_id=spec.location_id,
            stack_level=spec.stack_level,
            qr_id=spec.id,
        )
        for spec in seed_all_wips
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
    # 7~11. 시나리오/배치/절단/예상재공품/작업지시는 초기 비움
    # ─────────────────────────────────────────────────────────
    # 사용자 여정:
    #   1) Office에서 프로젝트 선택
    #   2) LANTEK import로 시나리오 생성
    #   3) 시나리오 발행 후 Field에서 작업 시작

    # ─────────────────────────────────────────────────────────
    # 데이터 커밋
    # ─────────────────────────────────────────────────────────
    await db.commit()
    print("✅ 시드 데이터 초기화 완료!")
