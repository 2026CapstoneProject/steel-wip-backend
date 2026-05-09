# app/seed.py
"""
데이터베이스 시드 데이터 초기화
백엔드 시작 시 호출되어 사용자 여정 데모용 기준 데이터를 삽입합니다.

- 시작 시점에는 시나리오/배치/작업지시가 하나도 없는 상태를 만든다.
- 생산계획자는 Office에서 LANTEK import → 시나리오 확인 → 발행을 수행한다.
- 작업자는 발행 이후에만 App(Field)에서 시나리오를 확인할 수 있다.
"""
# app/seed.py
import csv
from pathlib import Path
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from app.models import (
    Locations, QrCodes, Users, SteelWip, Projects, BatchItems,
    EstimatedWips, LazerCutting, Batch, Scenarios
)

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# CSV 파일들이 저장된 디렉토리 경로 (프로젝트 구조에 맞게 수정 가능)
CSV_DIR = Path(__file__).resolve().parent.parent / "seed"

print(f"CSV Directory Path: {CSV_DIR}")

def read_csv(filename: str) -> list:
    """CSV 파일을 읽어 딕셔너리 리스트로 반환 (다중 인코딩 지원)"""
    file_path = CSV_DIR / filename
    if not file_path.exists():
        print(f"Warning: {filename} not found at {file_path}")
        return []
        
    # 1. 먼저 utf-8-sig (보편적인 UTF-8)로 시도
    try:
        with open(file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            return list(reader)
    except UnicodeDecodeError:
        # 2. 실패 시 엑셀 기본 인코딩인 cp949(euc-kr)로 다시 시도
        with open(file_path, mode='r', encoding='cp949') as f:
            reader = csv.DictReader(f)
            return list(reader)

async def seed_database(db: AsyncSession) -> None:
    """데이터베이스를 초기화하고 CSV 샘플 데이터를 삽입합니다."""

    # ─────────────────────────────────────────────────────────
    # 1. 모든 테이블 데이터 역순 삭제 (기존 데이터 제거)
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
    await db.commit() # 삭제 후 커밋

    # ─────────────────────────────────────────────────────────
    # 2. Locations (창고 구역) - locations.csv 참조
    # ─────────────────────────────────────────────────────────
    loc_data = read_csv('locations.csv')
    locations = [
        Locations(
            id=int(row['id']), 
            loc_name=row['loc_name'],
            # csv에 없는 기본값 설정
            loc_can_stock=1 if 'LAZER' not in row['loc_name'] else 0,
            loc_stack_height=3 if 'LAZER' not in row['loc_name'] else 0
        ) for row in loc_data
    ]
    if locations:
        db.add_all(locations)
        await db.flush()

    # ─────────────────────────────────────────────────────────
    # 3. QR Codes - qr_codes.csv 참조
    # ─────────────────────────────────────────────────────────
    qr_data = read_csv('qr_codes.csv')
    qr_codes = [
        QrCodes(id=int(row['id']), qr_code=row['qr_code'])
        for row in qr_data
    ]
    if qr_codes:
        db.add_all(qr_codes)
        await db.flush()

    # ─────────────────────────────────────────────────────────
    # 4. Users - users.csv 참조
    # ─────────────────────────────────────────────────────────
    user_data = read_csv('users.csv')
    users = [
        Users(
            id=int(row['id']),
            username=row['username'],
            password=pwd_context.hash(row['password']),   # ← 평문을 bcrypt 해시로 변환
            department=row['department'],
            role=row['role'],
            user_num=int(row['user_num'])
        ) for row in user_data
    ]
    if users:
        db.add_all(users)
        await db.flush()

    # ─────────────────────────────────────────────────────────
    # 5. Projects - projects.csv 참조
    # ─────────────────────────────────────────────────────────
    project_data = read_csv('projects.csv')
    projects = [
        Projects(
            id=int(row['id']),
            title=row['title'],
            project_due=datetime.strptime(row['project_due'], '%Y-%m-%d').date()
        ) for row in project_data
    ]
    if projects:
        db.add_all(projects)
        await db.flush()

    # ─────────────────────────────────────────────────────────
    # 6. Steel WIPs - steel_wip.csv 참조
    # ─────────────────────────────────────────────────────────
    wip_data = read_csv('steel_wip.csv')
    steel_wips = []
    for row in wip_data:
        # 빈 문자열 처리
        loc_id = int(row['location_id']) if row['location_id'] else None
        stack_lvl = int(row['stack_level']) if row['stack_level'] else None
        q_id = int(row['qr_id']) if row['qr_id'] else None
        
        steel_wips.append(SteelWip(
            id=int(row['id']),
            status=row['status'],
            manufacturer=row['manufacturer'] if row['manufacturer'] else "POSCO", # 빈값이면 기본값
            material=row['material'],
            thickness=float(row['thickness']),
            width=float(row['width']),
            length=float(row['length']),
            weight=float(row['weight']),
            location_id=loc_id,
            stack_level=stack_lvl,
            qr_id=q_id
        ))
    if steel_wips:
        db.add_all(steel_wips)
        await db.flush()

    # ─────────────────────────────────────────────────────────
    # 데이터 커밋
    # ─────────────────────────────────────────────────────────
    await db.commit()
    print("✅ CSV 기반 시드 데이터 초기화 완료!")
