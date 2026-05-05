import io
import csv
import openpyxl
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import SteelWip, QrCodes, Locations, SteelWipStatus


# ── 파일 파싱 ────────────────────────────────────────────────────────────────

def parse_file(filename: str, content: bytes) -> list[dict]:
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext == "csv":
        for encoding in ("utf-8-sig", "cp949", "latin-1"):
            try:
                text = content.decode(encoding)
                reader = csv.DictReader(io.StringIO(text))
                return [row for row in reader]
            except (UnicodeDecodeError, Exception):
                continue
        raise ValueError("CSV 파일 인코딩을 인식할 수 없습니다. (utf-8, cp949 지원)")

    elif ext in ("xlsx", "xls"):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        result = [
            {headers[i]: rows[r][i] for i in range(len(headers))}
            for r in range(1, len(rows))
        ]
        wb.close()
        return result
    else:
        raise ValueError(f"지원하지 않는 파일 형식입니다: .{ext}")


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None or (isinstance(val, float) and val != val):
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _is_in_use(db_wip: SteelWip) -> bool:
    return db_wip.status in (SteelWipStatus.CONSUMED, SteelWipStatus.RESERVATED)


def _eq(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _parse_location(file_row: dict, loc_list) -> tuple[int | None, int | None]:
    """'위치' 컬럼(loc_name-stack_level)을 파싱하여 (location_id, stack_level) 반환"""
    raw = str(file_row.get("위치") or "").strip()
    if raw and "-" in raw:
        parts = raw.rsplit("-", 1)
        try:
            stack = int(parts[1])
            matched = next((l for l in loc_list if l.loc_name == parts[0]), None)
            if matched:
                return matched.id, stack
        except (ValueError, IndexError):
            pass
    return None, None


def _extract_fields(file_row: dict, loc_list, db_wip: SteelWip) -> dict:
    """업데이트용 — 위치가 빈칸이면 None으로 저장, 파싱 실패 시에만 DB 값 유지"""
    raw = str(file_row.get("위치") or "").strip()

    if raw == "":
        # 파일에 위치가 명시적으로 비어 있음 → None으로 저장
        location_id = None
        stack_level = None
    else:
        location_id, stack_level = _parse_location(file_row, loc_list)
        if location_id is None:
            # 값은 있지만 파싱 실패 → 기존 DB 값 유지
            location_id = db_wip.location_id
            stack_level  = db_wip.stack_level

    return {
        "material":     str(file_row.get("재질") or "").strip() or None,
        "thickness":    _to_float(file_row.get("두께")),
        "width":        _to_float(file_row.get("폭")),
        "length":       _to_float(file_row.get("길이")),
        "weight":       _to_float(file_row.get("재고중량")),
        "manufacturer": str(file_row.get("제조사") or "").strip() or None,
        "location_id":  location_id,
        "stack_level":  stack_level,
    }


def _extract_fields_new(file_row: dict, loc_list) -> dict:
    """신규 생성용 — 위치 빈칸이면 None으로 저장"""
    raw = str(file_row.get("위치") or "").strip()
    if raw == "":
        location_id, stack_level = None, None
    else:
        location_id, stack_level = _parse_location(file_row, loc_list)
        # 값은 있으나 파싱 실패해도 None으로 저장 (기존 DB 값 없음)

    return {
        "material":     str(file_row.get("재질") or "").strip() or None,
        "thickness":    _to_float(file_row.get("두께")),
        "width":        _to_float(file_row.get("폭")),
        "length":       _to_float(file_row.get("길이")),
        "weight":       _to_float(file_row.get("재고중량")),
        "manufacturer": str(file_row.get("제조사") or "").strip() or None,
        "location_id":  location_id,
        "stack_level":  stack_level,
    }


# ── 공통 데이터 빌드 ──────────────────────────────────────────────────────────

async def _build_common_data(db: AsyncSession, filename: str, content: bytes):
    rows = parse_file(filename, content)
    if not rows:
        raise ValueError("파일에 데이터가 없습니다.")

    required_cols = {"소재번호", "재질", "두께", "폭", "길이", "재고중량"}
    missing = required_cols - set(rows[0].keys())
    if missing:
        raise ValueError(f"파일에 필수 컬럼이 없습니다: {missing}")

    qr_result = await db.execute(select(QrCodes))
    qr_map: dict[str, QrCodes] = {
        r.qr_code: r for r in qr_result.scalars().all() if r.qr_code
    }

    loc_result = await db.execute(select(Locations))
    loc_list = loc_result.scalars().all()

    file_qr_set = {
        str(row.get("소재번호") or "").strip()
        for row in rows
        if str(row.get("소재번호") or "").strip()
    }

    return rows, qr_map, loc_list, file_qr_set


async def _build_missing_in_file(
    db: AsyncSession,
    qr_map: dict,
    file_qr_set: set,
) -> list[dict]:
    qr_id_to_code = {v.id: v.qr_code for v in qr_map.values()}
    all_wips_result = await db.execute(select(SteelWip))
    all_wips = all_wips_result.scalars().all()

    return [
        {
            "wip_id":    wip.id,
            "qr_code":   qr_id_to_code.get(wip.qr_id),
            "material":  wip.material,
            "thickness": wip.thickness,
            "width":     wip.width,
            "length":    wip.length,
            "weight":    wip.weight,
            "status":    wip.status.value,
        }
        for wip in all_wips
        if wip.qr_id and qr_id_to_code.get(wip.qr_id) not in file_qr_set
    ]


# ── 1단계: 미리보기 ──────────────────────────────────────────────────────────

async def preview_wip_file(db: AsyncSession, filename: str, content: bytes) -> dict:
    rows, qr_map, loc_list, file_qr_set = await _build_common_data(db, filename, content)

    to_update = []
    to_create = []
    skipped = []
    unchanged = 0

    for file_row in rows:
        qr_code_val = str(file_row.get("소재번호") or "").strip()
        if not qr_code_val:
            skipped.append({"qr_code": "(없음)", "reason": "소재번호(QR코드) 값이 비어 있습니다."})
            continue

        qr_obj = qr_map.get(qr_code_val)

        # ── QR 자체가 DB에 없음 → 신규 등록 후보
        if qr_obj is None:
            to_create.append({
                "qr_code": qr_code_val,
                "qr_id":   None,
                "fields":  _extract_fields_new(file_row, loc_list),
            })
            continue

        # ── QR은 있지만 연결된 WIP 없음 → 신규 등록 후보
        wip_result = await db.execute(select(SteelWip).where(SteelWip.qr_id == qr_obj.id))
        db_wip = wip_result.scalars().first()

        if db_wip is None:
            to_create.append({
                "qr_code": qr_code_val,
                "qr_id":   qr_obj.id,
                "fields":  _extract_fields_new(file_row, loc_list),
            })
            continue

        # ── 생산 투입 중 → 수정 불가
        if _is_in_use(db_wip):
            skipped.append({
                "qr_code": qr_code_val,
                "reason": f"이미 생산에 투입된 재고입니다. (현재 상태: {db_wip.status.value})",
            })
            continue

        # ── 변경 사항 비교
        update_fields = _extract_fields(file_row, loc_list, db_wip)
        is_same = all(
            _eq(getattr(db_wip, k), v)
            for k, v in update_fields.items()
            if v is not None
        )

        ALWAYS_COMPARE_FIELDS = {"location_id", "stack_level"}  # None이어도 비교할 필드

        is_same = all(
            _eq(getattr(db_wip, k), v)
            for k, v in update_fields.items()
            if v is not None or k in ALWAYS_COMPARE_FIELDS
        )
        to_update.append({
            "wip_id":  db_wip.id,
            "qr_code": qr_code_val,
            "before": {
                "material":     db_wip.material,
                "thickness":    db_wip.thickness,
                "width":        db_wip.width,
                "length":       db_wip.length,
                "weight":       db_wip.weight,
                "manufacturer": db_wip.manufacturer,
                "location_id":  db_wip.location_id,
                "stack_level":  db_wip.stack_level,
            },
            "after": update_fields,
        })

    missing_in_file = await _build_missing_in_file(db, qr_map, file_qr_set)

    return {
        "to_update":       to_update,
        "to_create":       to_create,
        "skipped":         skipped,
        "unchanged":       unchanged,
        "missing_in_file": missing_in_file,
    }


# ── 2단계: 확정 반영 ─────────────────────────────────────────────────────────

async def confirm_wip_updates(
    db: AsyncSession,
    updates: list[dict],
    creates: list[dict],
) -> dict:
    updated = []
    skipped = []

    # 수정
    for item in updates:
        wip_id = item.get("wip_id")
        new_fields: dict = item.get("after", {})

        result = await db.execute(select(SteelWip).where(SteelWip.id == wip_id))
        db_wip = result.scalars().first()

        if db_wip is None:
            skipped.append({"wip_id": wip_id, "reason": "존재하지 않는 재공품입니다."})
            continue

        if _is_in_use(db_wip):
            skipped.append({
                "wip_id": wip_id,
                "reason": f"이미 생산에 투입된 재고입니다. (현재 상태: {db_wip.status.value})",
            })
            continue

        NULLABLE_FIELDS = {"location_id", "stack_level", "manufacturer"}  # None 허용 필드

        for field, value in new_fields.items():
            if not hasattr(db_wip, field):
                continue
            # None 허용 필드이거나 값이 있는 경우에만 반영
            if value is not None or field in NULLABLE_FIELDS:
                setattr(db_wip, field, value)
                updated.append(wip_id)

    # 신규 생성
    created = []
    for item in creates:
        qr_code_val = item.get("qr_code")
        fields: dict = item.get("fields", {})
        qr_id = item.get("qr_id")

        # QR이 DB에 없으면 새로 생성
        if qr_id is None:
            new_qr = QrCodes(qr_code=qr_code_val)
            db.add(new_qr)
            await db.flush()  # id 발급
            qr_id = new_qr.id

        new_wip = SteelWip(
            qr_id=qr_id,
            status=SteelWipStatus.IN_STOCK,
            material=fields.get("material") or "",
            thickness=fields.get("thickness") or 0,
            width=fields.get("width") or 0,
            length=fields.get("length") or 0,
            weight=fields.get("weight") or 0,
            manufacturer=fields.get("manufacturer"),
            location_id=fields.get("location_id"),
            stack_level=fields.get("stack_level"),
        )
        db.add(new_wip)
        created.append(qr_code_val)

    await db.commit()
    return {"updated": updated, "skipped": skipped, "created": created}


# ── 삭제 + 층수 재정렬 + QR 삭제 ─────────────────────────────────────────────

async def delete_wip_with_reorder(db: AsyncSession, wip_ids: list[int]) -> dict:
    """
    1. 생산 투입 중이면 삭제 거부
    2. WIP 삭제
    3. 연결된 QR 코드 삭제
    4. 동일 location_id 내 남은 WIP의 stack_level을 1부터 빈 틈 없이 재정렬
    """
    deleted = []
    skipped = []
    affected_locations: set[int] = set()

    for wip_id in wip_ids:
        result = await db.execute(select(SteelWip).where(SteelWip.id == wip_id))
        wip = result.scalars().first()

        if wip is None:
            skipped.append({"wip_id": wip_id, "reason": "존재하지 않는 재공품입니다."})
            continue

        if _is_in_use(wip):
            skipped.append({
                "wip_id": wip_id,
                "reason": f"이미 생산에 투입된 재고는 삭제할 수 없습니다. (현재 상태: {wip.status.value})",
            })
            continue

        location_id = wip.location_id

        # QR 삭제
        if wip.qr_id is not None:
            qr_result = await db.execute(select(QrCodes).where(QrCodes.id == wip.qr_id))
            qr_obj = qr_result.scalars().first()
            if qr_obj:
                await db.delete(qr_obj)

        await db.delete(wip)
        deleted.append(wip_id)

        if location_id is not None:
            affected_locations.add(location_id)

    # 삭제 반영 후 재정렬
    await db.flush()

    for loc_id in affected_locations:
        remaining_result = await db.execute(
            select(SteelWip)
            .where(SteelWip.location_id == loc_id)
            .order_by(SteelWip.stack_level.asc())
        )
        remaining_wips = remaining_result.scalars().all()

        for new_level, wip in enumerate(remaining_wips, start=1):
            if wip.stack_level != new_level:
                wip.stack_level = new_level

    await db.commit()
    return {"deleted": deleted, "skipped": skipped}