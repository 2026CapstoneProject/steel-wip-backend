# tests/load_fixtures.py
"""
capstoneDB-backup 폴더의 MySQL dump 파일을 SQLite 테스트 DB에 로드하는 유틸리티.

[왜 직접 파싱하는가]
  MySQL dump는 CREATE TABLE, LOCK TABLES, /*!...*/ 등 MySQL 전용 문법을 포함하고 있어
  SQLite에 그대로 실행할 수 없다. 또한 dump의 CREATE TABLE 컬럼 순서가 SQLAlchemy
  모델과 다를 수 있으므로, INSERT VALUES만 뽑아 명시적 컬럼명을 붙여 재조립한다.

[처리 흐름]
  1. CREATE TABLE 구문에서 컬럼 이름 목록(순서 포함) 추출
  2. INSERT INTO ... VALUES ... 구문에서 행 데이터 추출
  3. INSERT INTO table (col1, col2, ...) VALUES (...) 형태로 재조립
  4. SAVEPOINT를 활용해 행 단위 fault-tolerance 적용
     (NULL in NOT NULL 컬럼, FK 위반 등 문제 행은 건너뜀)
"""

import re
import pathlib
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, func

from app.models import (
    Locations,
    Projects,
    QrCodes,
    SteelWip,
    Scenarios,
    Batch,
    BatchItems,
    LazerCutting,
    EstimatedWips,
)

# capstoneDB-backup 폴더 경로 (steel-wip-backend 기준 상위)
DUMP_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "capstoneDB-backup"
)

# FK 의존관계 순서대로 로드
LOAD_ORDER = [
    ("capstone2026_users.sql",          "users"),
    ("capstone2026_locations.sql",       "locations"),
    ("capstone2026_qr_codes.sql",        "qr_codes"),
    ("capstone2026_projects.sql",        "projects"),
    ("capstone2026_steel_wip.sql",       "steel_wip"),
    ("capstone2026_scenarios.sql",       "scenarios"),
    ("capstone2026_batch.sql",           "batch"),
    ("capstone2026_batch_items.sql",     "batch_items"),
    ("capstone2026_lazer_cutting.sql",   "lazer_cutting"),
    ("capstone2026_estimated_wips.sql",  "estimated_wips"),
    ("capstone2026_steel_wip_history.sql", "steel_wip_history"),
    # trigger 파일은 SQLite에서 지원하지 않으므로 제외
]


async def _load_embedded_field_fixtures(session: AsyncSession) -> None:
    """
    capstoneDB-backup 폴더가 없는 개발 환경에서도
    field 통합 테스트가 기대하는 최소 실데이터 셋을 구성한다.
    """
    location_rows = [
        (1, "A-1"), (2, "A-2"), (3, "A-3"), (4, "A-4"),
        (5, "B-1"), (6, "B-2"), (7, "B-3"), (8, "C-1"), (9, "C-2"),
        (15, "S4-1"), (16, "S4-2"), (17, "S4-3"), (18, "S4-4"), (23, "ETC"),
    ]
    session.add_all(
        Locations(id=loc_id, loc_name=name, loc_can_stock=1, loc_stack_height=10)
        for loc_id, name in location_rows
    )

    session.add_all([
        Projects(id=1, title="포스코 건설(80톤)", project_due=date(2026, 4, 30)),
        Projects(id=2, title="토네이도 건설(12톤)", project_due=date(2026, 5, 10)),
    ])

    session.add_all([
        Scenarios(
            id=1,
            title="포스코 건설(80톤)-1",
            status="ORDERED",
            scenario_due=date(2026, 4, 20),
            scenario_order=1,
            created_at=datetime(2026, 4, 5, 9, 0, 0),
            lazer_name="LAZER1",
            project_id=1,
            emergency_or_not=0,
        ),
        Scenarios(
            id=2,
            title="토네이도 건설(12톤)-1",
            status="ORDERED",
            scenario_due=date(2026, 4, 25),
            scenario_order=2,
            created_at=datetime(2026, 4, 5, 10, 0, 0),
            lazer_name="LAZER1",
            project_id=2,
            emergency_or_not=0,
        ),
    ])

    session.add_all([
        Batch(id=1, scenario_id=1, batch_order=1),
        Batch(id=2, scenario_id=1, batch_order=2),
        Batch(id=3, scenario_id=1, batch_order=3),
        Batch(id=4, scenario_id=2, batch_order=1),
        Batch(id=5, scenario_id=2, batch_order=2),
        Batch(id=6, scenario_id=2, batch_order=3),
    ])

    session.add_all(QrCodes(id=qr_id, qr_code=f"QR-{qr_id}") for qr_id in range(103, 109))

    def build_wip(
        wip_id: int,
        location_id: int | None = 1,
        qr_id: int | None = None,
        thickness: float = 20.0,
        width: float = 2438.0,
        length: float = 6096.0,
        material: str = "SM355A",
        status: str = "IN_STOCK",
    ) -> SteelWip:
        return SteelWip(
            id=wip_id,
            status=status,
            material=material,
            thickness=thickness,
            width=width,
            length=length,
            weight=100.0,
            manufacturer="POSCO",
            location_id=location_id,
            stack_level=1,
            qr_id=qr_id,
        )

    core_wips = [
        build_wip(1, 1),
        build_wip(4, 2),
        build_wip(39, 3),
        build_wip(42, 3, material="GS400"),
        build_wip(103, None, 103, 16.0, 1446.4, 1511.0, status="REGISTERED"),
        build_wip(104, None, 104, 12.0, 1200.0, 2200.0, status="REGISTERED"),
        build_wip(105, None, 105, 14.0, 1800.0, 3000.0, status="REGISTERED"),
        build_wip(106, None, 106, 10.0, 900.0, 2100.0, status="REGISTERED"),
        build_wip(107, None, 107, 8.0, 800.0, 1600.0, status="REGISTERED"),
        build_wip(108, None, 108, 6.0, 700.0, 1400.0, status="REGISTERED"),
    ]
    filler_ids = list(range(200, 343))
    core_wips.extend(build_wip(wip_id, (wip_id % 9) + 1) for wip_id in filler_ids)
    session.add_all(core_wips)

    def add_item(
        item_id: int,
        batch_id: int,
        order: int,
        action: str,
        from_location: int | None,
        to_location: int | None,
        steel_wip_id: int | None = None,
    ) -> BatchItems:
        return BatchItems(
            id=item_id,
            batch_id=batch_id,
            steel_wip_id=steel_wip_id,
            batch_item_action=action,
            status="PENDING",
            batch_item_order=order,
            from_location=from_location,
            to_location=to_location,
            expected_start_time=(order - 1) * 5,
            expected_running_time=5,
        )

    items: list[BatchItems] = []

    batch1_plan = {
        3: ("PICKING", 3, 15, 42),
        7: ("PICKING", 5, 16, 200),
        8: ("INBOUND", None, 8, 103),
        9: ("INBOUND", None, 1, 104),
        10: ("INBOUND", None, 4, 105),
        11: ("INBOUND", None, 7, 106),
        18: ("PICKING", 6, 17, 201),
        26: ("INBOUND", None, 8, 107),
        27: ("INBOUND", None, 3, 108),
        28: ("PICKING", 7, 18, 202),
    }
    relocate_wips = iter(range(203, 223))
    for item_id in range(1, 31):
        if item_id in batch1_plan:
            action, from_loc, to_loc, wip_id = batch1_plan[item_id]
        else:
            action, from_loc, to_loc, wip_id = (
                "RELOCATE",
                ((item_id - 1) % 8) + 1,
                ((item_id + 2) % 8) + 1,
                next(relocate_wips),
            )
        items.append(add_item(item_id, 1, item_id, action, from_loc, to_loc, wip_id))

    batch2_plan = {
        31: ("RELOCATE", 1, 8, 223),
        32: ("RELOCATE", 2, 9, 224),
        33: ("PICKING", 6, 15, 225),
        34: ("PICKING", 5, 16, 226),
        35: ("PICKING", 7, 17, 227),
        36: ("PICKING", 3, 18, 228),
        37: ("INBOUND", None, 1, 229),
        38: ("INBOUND", None, 4, 230),
        39: ("INBOUND", None, 7, 231),
        40: ("INBOUND", None, 8, 232),
        41: ("INBOUND", None, 9, 233),
    }
    relocate_wips = iter(range(234, 242))
    for item_id in range(31, 50):
        if item_id in batch2_plan:
            action, from_loc, to_loc, wip_id = batch2_plan[item_id]
        else:
            action, from_loc, to_loc, wip_id = (
                "RELOCATE",
                ((item_id - 30) % 7) + 1,
                ((item_id - 27) % 7) + 1,
                next(relocate_wips),
            )
        items.append(add_item(item_id, 2, item_id - 30, action, from_loc, to_loc, wip_id))

    batch3_plan = {
        63: ("PICKING", 1, 15, 242),
        64: ("PICKING", 2, 16, 243),
        65: ("PICKING", 3, 17, 244),
        66: ("PICKING", 4, 18, 245),
        67: ("INBOUND", None, 1, 246),
        68: ("INBOUND", None, 5, 247),
        69: ("INBOUND", None, 8, 248),
    }
    relocate_wips = iter(range(249, 262))
    for item_id in range(50, 70):
        if item_id in batch3_plan:
            action, from_loc, to_loc, wip_id = batch3_plan[item_id]
        else:
            action, from_loc, to_loc, wip_id = (
                "RELOCATE",
                ((item_id - 48) % 8) + 1,
                ((item_id - 45) % 8) + 1,
                next(relocate_wips),
            )
        items.append(add_item(item_id, 3, item_id - 49, action, from_loc, to_loc, wip_id))

    scenario2_specs = [(4, 70, 36), (5, 106, 30), (6, 136, 15)]
    filler_wips = iter(range(262, 343))
    for batch_id, start_id, count in scenario2_specs:
        for index in range(count):
            item_id = start_id + index
            items.append(
                add_item(
                    item_id,
                    batch_id,
                    index + 1,
                    "RELOCATE",
                    (index % 8) + 1,
                    ((index + 3) % 8) + 1,
                    next(filler_wips),
                )
            )

    session.add_all(items)

    session.add_all([
        LazerCutting(id=1, batch_id=1, scenario_id=1, steel_wip_id=42, estimated_cutting_time=23, status="PENDING"),
        LazerCutting(id=2, batch_id=1, scenario_id=1, steel_wip_id=1, estimated_cutting_time=93, status="PENDING"),
        LazerCutting(id=3, batch_id=1, scenario_id=1, steel_wip_id=4, estimated_cutting_time=32, status="PENDING"),
        LazerCutting(id=4, batch_id=1, scenario_id=1, steel_wip_id=39, estimated_cutting_time=43, status="PENDING"),
    ])

    session.add_all([
        EstimatedWips(id=1, lazer_cutting_id=1, qr_id=103, material="SM355A", thickness=16.0, width=1446.4, length=1511.0),
        EstimatedWips(id=2, lazer_cutting_id=1, qr_id=104, material="SM355A", thickness=12.0, width=1200.0, length=2200.0),
        EstimatedWips(id=3, lazer_cutting_id=2, qr_id=105, material="SM355A", thickness=14.0, width=1800.0, length=3000.0),
        EstimatedWips(id=4, lazer_cutting_id=2, qr_id=106, material="SM355A", thickness=10.0, width=900.0, length=2100.0),
        EstimatedWips(id=5, lazer_cutting_id=3, qr_id=107, material="SM355A", thickness=8.0, width=800.0, length=1600.0),
        EstimatedWips(id=6, lazer_cutting_id=3, qr_id=108, material="SM355A", thickness=6.0, width=700.0, length=1400.0),
    ])

    await session.commit()


# ── 파싱 헬퍼 ─────────────────────────────────────────────────────────

def _extract_columns(sql: str) -> list[str]:
    """
    CREATE TABLE 구문에서 컬럼 이름 목록(선언 순서)을 추출한다.
    PRIMARY KEY, KEY, CONSTRAINT 등 제약조건 라인은 제외한다.
    """
    m = re.search(
        r'CREATE TABLE[^(]+\((.*?)\)\s*ENGINE',
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []

    columns: list[str] = []
    for line in m.group(1).splitlines():
        stripped = line.strip().rstrip(',')
        upper = stripped.upper()
        # 제약조건 라인 건너뜀
        if any(upper.startswith(kw) for kw in
               ('PRIMARY KEY', 'KEY ', 'UNIQUE', 'CONSTRAINT', 'INDEX')):
            continue
        col_m = re.match(r'^`(\w+)`', stripped)
        if col_m:
            columns.append(col_m.group(1))

    return columns


def _split_value_rows(values_block: str) -> list[str]:
    """
    VALUES 절 문자열을 개별 행 문자열로 분리한다.
    단순 split은 문자열 내 콤마/괄호를 잘못 분리하므로 상태 머신으로 처리.

    예) "(1,'a',NULL),(2,'b,c',3)"
      → ["(1,'a',NULL)", "(2,'b,c',3)"]
    """
    rows: list[str] = []
    depth = 0
    in_str = False
    escape = False
    buf: list[str] = []

    for ch in values_block:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == '\\' and in_str:
            buf.append(ch)
            escape = True
            continue
        if ch == "'" and not in_str:
            in_str = True
            buf.append(ch)
            continue
        if ch == "'" and in_str:
            in_str = False
            buf.append(ch)
            continue
        if in_str:
            buf.append(ch)
            continue

        if ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
            if depth == 0:
                row = ''.join(buf).strip()
                if row:
                    rows.append(row)
                buf = []
        elif ch == ',' and depth == 0:
            pass  # 행 구분자 — 버퍼에 넣지 않음
        else:
            buf.append(ch)

    return rows


def _parse_dump_file(sql: str, table_name: str) -> tuple[list[str], list[str]]:
    """
    dump 파일 내용에서 (columns, rows) 쌍을 반환한다.
    columns : 컬럼 이름 리스트 (MySQL CREATE TABLE 선언 순서)
    rows    : 각 행의 값 문자열 리스트 ex) "(1,'abc',NULL)"
    """
    columns = _extract_columns(sql)
    if not columns:
        return [], []

    m = re.search(
        r'INSERT INTO `' + re.escape(table_name) + r'` VALUES\s*(.*?);',
        sql,
        re.DOTALL,
    )
    if not m:
        return columns, []

    rows = _split_value_rows(m.group(1).strip())
    return columns, rows


# ── 공개 API ──────────────────────────────────────────────────────────

async def load_dump_fixtures(session: AsyncSession) -> None:
    """
    LOAD_ORDER에 정의된 순서대로 dump 파일을 읽어 SQLite 세션에 INSERT한다.

    각 행은 SAVEPOINT 안에서 실행되어, 실패한 행만 건너뛰고 나머지는 계속 삽입된다.
    (예: scenarios 테이블의 status=NULL 행은 NOT NULL 위반으로 skip됨)
    """
    for filename, table_name in LOAD_ORDER:
        filepath = DUMP_DIR / filename
        if not filepath.exists():
            continue

        sql_content = filepath.read_text(encoding="utf-8")
        columns, rows = _parse_dump_file(sql_content, table_name)
        if not columns or not rows:
            continue

        # 명시적 컬럼명 포함 INSERT 접두어 (backtick — SQLite 호환)
        col_clause = ", ".join(f"`{c}`" for c in columns)
        prefix = f"INSERT INTO `{table_name}` ({col_clause}) VALUES "

        for row_values in rows:
            stmt = prefix + row_values
            try:
                # SAVEPOINT로 행 단위 rollback 보장
                async with session.begin_nested():
                    await session.execute(text(stmt))
            except Exception:
                # NOT NULL 위반, FK 위반 등 → 이 행만 건너뜀
                pass

    await session.commit()

    scenario_count = (await session.execute(select(func.count(Scenarios.id)))).scalar() or 0
    if scenario_count == 0:
        await _load_embedded_field_fixtures(session)
