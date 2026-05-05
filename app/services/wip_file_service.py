# app/services/wip_file_service.py
import io
import csv
import openpyxl
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import SteelWip, QrCodes, Locations, SteelWipStatus


def parse_file(filename: str, content: bytes) -> list[dict]:
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext == "csv":
        # 인코딩 순서대로 시도: utf-8-sig → cp949(euc-kr) → latin-1(fallback)
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

def _to_float(val) -> float | None:
    if val is None or (isinstance(val, float) and val != val):  # NaN check
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


def _extract_fields(file_row: dict, loc_list, db_wip: SteelWip) -> dict:
    """파일 한 행에서 업데이트할 필드값을 추출"""
    file_location_id = db_wip.location_id
    file_stack_level = db_wip.stack_level

    file_location_raw = str(file_row.get("위치") or "").strip()
    if file_location_raw and "-" in file_location_raw:
        parts = file_location_raw.rsplit("-", 1)
        try:
            stack_from_file = int(parts[1])
            matched_loc = next((l for l in loc_list if l.loc_name == parts[0]), None)
            if matched_loc:
                file_location_id = matched_loc.id
                file_stack_level = stack_from_file
        except (ValueError, IndexError):
            pass

    return {
        "material":     str(file_row.get("재질") or "").strip() or None,
        "thickness":    _to_float(file_row.get("두께")),
        "width":        _to_float(file_row.get("폭")),
        "length":       _to_float(file_row.get("길이")),
        "weight":       _to_float(file_row.get("재고중량")),
        "manufacturer": str(file_row.get("제조사") or "").strip() or None,
        "location_id":  file_location_id,
        "stack_level":  file_stack_level,
    }
def _extract_fields_new(file_row: dict, loc_list) -> dict:
    """DB WIP 없이 파일 행에서 필드값만 추출 (신규 생성용)"""
    location_id = None
    stack_level = None

    file_location_raw = str(file_row.get("위치") or "").strip()
    if file_location_raw and "-" in file_location_raw:
        parts = file_location_raw.rsplit("-", 1)
        try:
            stack_from_file = int(parts[1])
            matched_loc = next((l for l in loc_list if l.loc_name == parts[0]), None)
            if matched_loc:
                location_id = matched_loc.id
                stack_level = stack_from_file
        except (ValueError, IndexError):
            pass

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

async def _build_common_data(db: AsyncSession, filename: str, content: bytes):
    """파싱 + DB 조회 공통 로직"""
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


# ── 1단계: 미리보기 (DB 변경 없음) ──────────────────────────────────────────

async def preview_wip_file(db: AsyncSession, filename: str, content: bytes) -> dict:
    rows, qr_map, loc_list, file_qr_set = await _build_common_data(db, filename, content)

    to_update = []
    to_create = []   # ← 추가
    skipped = []
    unchanged = 0

    for file_row in rows:
        qr_code_val = str(file_row.get("소재번호") or "").strip()
        if not qr_code_val:
            skipped.append({"qr_code": "(없음)", "reason": "소재번호(QR코드) 값이 비어 있습니다."})
            continue

        qr_obj = qr_map.get(qr_code_val)
        new_fields = _extract_fields_new(file_row, loc_list)  # ← loc_list 전달

        if qr_obj is None:
            to_create.append({
                "qr_code": qr_code_val,
                "qr_id":   None,
                "fields":  new_fields,
            })
            continue

        wip_result = await db.execute(select(SteelWip).where(SteelWip.qr_id == qr_obj.id))
        db_wip = wip_result.scalars().first()

        if db_wip is None:
            to_create.append({
                "qr_code": qr_code_val,
                "qr_id":   qr_obj.id,
                "fields":  new_fields,
            })
            continue

        if _is_in_use(db_wip):
            skipped.append({
                "qr_code": qr_code_val,
                "reason": f"이미 생산에 투입된 재고입니다. (현재 상태: {db_wip.status.value})",
            })
            continue

        is_same = all([
            _eq(getattr(db_wip, k), v) for k, v in new_fields.items() if v is not None
        ])

        if is_same:
            unchanged += 1
            continue

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
            "after": new_fields,
        })

    missing_in_file = await _build_missing_in_file(db, qr_map, file_qr_set)

    return {
        "to_update":       to_update,
        "to_create":       to_create,   # ← 추가
        "skipped":         skipped,
        "unchanged":       unchanged,
        "missing_in_file": missing_in_file,
    }

# ── 2단계: 확정 반영 (실제 DB 업데이트) ──────────────────────────────────────

async def confirm_wip_file(db: AsyncSession, wip_ids: list[int]) -> dict:
    """사용자가 선택한 wip_id에 대해, preview 때 계산된 값을 재계산하여 반영.
    
    ※ preview 결과를 서버에 저장하지 않으므로 DB를 다시 조회하여 재검증합니다.
    """
    # confirm 요청에는 변경할 필드값도 함께 받아야 합니다.
    # → 아래 confirm_wip_updates 함수 참고
    pass


async def confirm_wip_updates(
    db: AsyncSession,
    updates: list[dict],  # [{"wip_id": 1, "after": {...}}, ...]
    creates: list[dict], 
) -> dict:
    """
    프론트에서 preview 응답의 to_update 중 사용자가 선택한 항목만 전달.
    각 항목의 after 값을 서버에서 재검증 후 DB에 반영.
    """
    updated = []
    skipped = []

    for item in updates:
        wip_id = item.get("wip_id")
        new_fields: dict = item.get("after", {})

        result = await db.execute(select(SteelWip).where(SteelWip.id == wip_id))
        db_wip = result.scalars().first()

        if db_wip is None:
            skipped.append({"wip_id": wip_id, "reason": "존재하지 않는 재공품입니다."})
            continue

        # 재검증: 생산 투입 여부
        if _is_in_use(db_wip):
            skipped.append({
                "wip_id": wip_id,
                "reason": f"이미 생산에 투입된 재고입니다. (현재 상태: {db_wip.status.value})",
            })
            continue

        for field, value in new_fields.items():
            if value is not None and hasattr(db_wip, field):
                setattr(db_wip, field, value)

        updated.append(wip_id)

    created = []
    for item in creates:
        qr_code_val = item.get("qr_code")
        fields: dict = item.get("fields", {})
        qr_id = item.get("qr_id")  # QR이 이미 있는 경우

        # QR코드가 DB에 없으면 새로 생성
        if qr_id is None:
            new_qr = QrCodes(qr_code=qr_code_val)
            db.add(new_qr)
            await db.flush()   # id 발급
            qr_id = new_qr.id

        # location_id 파싱 (위치 문자열로부터)
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

    return {
        "updated": updated,
        "skipped": skipped,
        "created": created,  
    }