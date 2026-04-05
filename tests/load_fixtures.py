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

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

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
