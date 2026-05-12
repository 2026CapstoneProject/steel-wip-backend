# app/services/lantek_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import random
import re
from typing import Iterable

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Projects, Scenarios, LazerCutting, EstimatedWips, QrCodes, SteelWip, Batch, BatchItems, Locations
from app.schemas.batch_item import BatchItemStatus
from app.schemas.enums import BatchActionType
from app.schemas.enums import WipStatus
from app.schemas.lantek import (
    LantekScenarioData,
    LantekCutting,
    LantekInput,
    LantekEstimatedWip,
)


STEEL_DENSITY = 7.85 / 1_000_000
PICKING_DESTINATION_NAMES = ["S4-1", "S4-2", "S4-3", "S4-4"]

# 파일 상단 상수 영역에 추가
RAW_MATERIAL_SIZES = {
    frozenset({2438.0, 6096.0}),    # 2438x6096 또는 6096x2438
    frozenset({2438.0, 12192.0}),   # 2438x12192 또는 12192x2438
}

def _determine_material_type(width: float, height: float) -> str:
    """폭/길이 순서 무관하게 원자재 여부 판단"""
    size_set = frozenset({round(width), round(height)})
    return "원자재" if size_set in RAW_MATERIAL_SIZES else "재공품"

@dataclass
class ParsedLantekLayout:
    layout_name: str
    slab_width: float
    slab_length: float
    plate_width: float
    plate_length: float
    thickness: float
    material: str
    estimated_minutes: int
    nc_code: str | None = None          # ← 추가
    order_name: str | None = None       # ← 추가 (오더명)
    job_name: str | None = None
    planned_source_wip_id: int | None = None
    planned_output_wip_id: int | None = None
    output_width: float | None = None
    output_length: float | None = None
    output_parts: list | None = None    # ← 추가 (단품 리스트: [{name, qr_code, width, height, weight}])
    input_width: float = 0.0    # ← 추가: input에 표시될 판재 폭
    input_length: float = 0.0   # ← 추가: input에 표시될 판재 길이

DEMO_IMPORT_JOBS = [
    {
        "jobName": "Job1",
        "plannedSourceWipId": 0,
        "plannedOutputWipId": 0,
        "material": "SM355A",
        "thickness": 12.0,
        "width": 2438.0,
        "height": 6096.0,
        "estimatedMinutes": 241,
    },
    {
        "jobName": "Job2",
        "plannedSourceWipId": 28,
        "plannedOutputWipId": 103,
        "material": "SM355A",
        "thickness": 12.0,
        "width": 950.0,
        "height": 2530.0,
        "estimatedMinutes": 10,
    },
    {
        "jobName": "Job3",
        "plannedSourceWipId": 99,
        "plannedOutputWipId": 104,
        "material": "SS275",
        "thickness": 20.0,
        "width": 570.0,
        "height": 2450.0,
        "estimatedMinutes": 4,
    },
]


def _normalize_pdf_text(text: str) -> str:
    return (
        text.replace("\xa0", " ")
        .replace("\u3000", " ")
        .replace("\r", "\n")
        .replace("m\nm", "mm")
    )


def _extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_layouts_from_text(text: str) -> list[ParsedLantekLayout]:
    """
    실제 LANTEK CUTTING PLAN PDF 포맷 파싱.
    PDF 1장 = 판재(자재) 1개. 섹션 분리 없이 전체 텍스트를 하나의 레이아웃으로 파싱.
    """
    normalized = _normalize_pdf_text(text)

    if not re.search(r"O\d{4,6}", normalized) and "CNC" not in normalized and "CUTTING" not in normalized.upper():
        return []

    # ── NC코드 ──
    nc_code_match = re.search(r"(O\d{4,6})", normalized)
    nc_code = nc_code_match.group(1).strip() if nc_code_match else None

    # ── 절단예상시간 ──
    time_match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?", normalized)
    estimated_minutes = (
        int(time_match.group(1)) * 60 + int(time_match.group(2))
        if time_match else 1
    )
    estimated_minutes = max(1, estimated_minutes)

    # ── 자재 정보: V2 포맷 우선 (cp3: "자재 20 Tx 6096 x 2438") ──
    material_info_match = re.search(
        r"자재\s+([0-9.]+)\s*[Tt]\s*[xX×]?\s*([0-9.]+)\s*[xX×]\s*([0-9.]+)",
        normalized,
    )
    if material_info_match:
        thickness = float(material_info_match.group(1))
        slab_width = float(material_info_match.group(2))
        slab_length = float(material_info_match.group(3))
        material_match = re.search(r"재질\s*([A-Za-z][A-Za-z0-9]+)", normalized)
        material = material_match.group(1) if material_match else "UNKNOWN"
    else:
        # ── V1 압축 포맷 (cp1, cp2: "SM355A20243860966...") ──
        v1_match = re.search(
            r"(S[A-Z]\d{3}[A-Z]?)\s*(\d{2})\s*(2438|6096|12192)\s*(2438|6096|12192)",
            normalized,
        )
        if not v1_match:
            return []
        material = v1_match.group(1)
        thickness = float(v1_match.group(2))
        slab_width = float(v1_match.group(3))
        slab_length = float(v1_match.group(4))

    # ── 오더명 ──
    order_match = re.search(r"오더\s+(?:\d+\s+)?(.+?)(?:\s+\d{3,}|\s*\||\n)", normalized)
    order_name = order_match.group(1).strip() if order_match else None

    # ── 단품 테이블 파싱: 단품명이 "재공품"이고 QR코드가 있는 행만 파싱 ──
    output_parts = []
    table_match = re.search(
        r"No\.\s+단\s*품\s*명.*",
        normalized,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if table_match:
        table_text = table_match.group(0)
        row_pattern = re.compile(
            r"\d+\s+재공품\s+(\S+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+[0-9.]+\s+[0-9.]+\s+(\d+)\s*[xX×]\s*(\d+)"
        )
        for row_match in row_pattern.finditer(table_text):
            output_parts.append({
                "name": "재공품",
                "qr_code": row_match.group(1).strip(),
                "width": float(row_match.group(5)),
                "height": float(row_match.group(6)),
                "weight": float(row_match.group(4)),
            })

    return [
        ParsedLantekLayout(
            layout_name=nc_code or "layout-1",
            slab_width=slab_width,
            slab_length=slab_length,
            plate_width=slab_width,
            plate_length=slab_length,
            input_width=slab_width,    # ← 추가
            input_length=slab_length,  # ← 추가
            thickness=thickness,
            material=material,
            estimated_minutes=estimated_minutes,
            nc_code=nc_code,
            order_name=order_name,
            output_parts=output_parts,
        )
    ]

def _calculate_weight(thickness: float, width: float, length: float) -> float:
    return round(thickness * width * length * STEEL_DENSITY, 1)


def _pick_matching_wip(
    layouts: Iterable[ParsedLantekLayout],
    stock_wips: list[SteelWip],
) -> list[tuple[ParsedLantekLayout, SteelWip]]:
    available = list(stock_wips)
    all_stock = list(stock_wips)
    assignments: list[tuple[ParsedLantekLayout, SteelWip]] = []

    for layout in layouts:
        exact_match = next(
            (
                wip
                for wip in available
                if wip.material == layout.material
                and float(wip.thickness or 0) == layout.thickness
                and float(wip.width or 0) >= layout.plate_width
                and float(wip.length or 0) >= layout.plate_length
            ),
            None,
        )
        fallback_match = next(
            (
                wip
                for wip in available
                if wip.material == layout.material
                and float(wip.thickness or 0) == layout.thickness
            ),
            None,
        )
        reused_exact_match = next(
            (
                wip
                for wip in all_stock
                if wip.material == layout.material
                and float(wip.thickness or 0) == layout.thickness
                and float(wip.width or 0) >= layout.plate_width
                and float(wip.length or 0) >= layout.plate_length
            ),
            None,
        )
        reused_fallback_match = next(
            (
                wip
                for wip in all_stock
                if wip.material == layout.material
                and float(wip.thickness or 0) == layout.thickness
            ),
            None,
        )
        selected = (
            exact_match
            or fallback_match
            or reused_exact_match
            or reused_fallback_match
            or (all_stock[0] if all_stock else None)
        )
        if selected is None:
            raise ValueError("LANTEK 결과를 배정할 가용 가능한 재고(IN_STOCK)가 부족합니다.")
        if selected in available:
            available.remove(selected)
        assignments.append((layout, selected))

    return assignments

async def _create_parsed_lantek_data(
    db: AsyncSession,
    scenario: Scenarios,
    layouts: list[ParsedLantekLayout],
) -> None:
    stock_stmt = (
        select(SteelWip)
        .where(SteelWip.status == WipStatus.IN_STOCK.value)
        .order_by(SteelWip.id.asc())
    )
    stock_wips = (await db.execute(stock_stmt)).scalars().all()
    if not stock_wips:
        raise ValueError("가용 가능한 재고(IN_STOCK)가 존재하지 않습니다.")

    assignments = _pick_matching_wip(layouts, stock_wips)

    for index, (layout, target_wip) in enumerate(assignments, start=1):
        cutting = LazerCutting(
            scenario_id=scenario.id,
            status="PENDING",
            priority="LOW",
            estimated_cutting_time=layout.estimated_minutes,
            steel_wip_id=target_wip.id,
            nc_code=layout.nc_code,
            input_material=layout.material,        # ← 추가
            input_width=layout.input_width,        # ← 추가
            input_length=layout.input_length,      # ← 추가
        )
        db.add(cutting)
        await db.flush()

        # 단품 테이블에서 파싱된 재공품만 EstimatedWips로 저장
        if layout.output_parts:
            for part in layout.output_parts:
                if not part["qr_code"]:   # ← QR코드 없으면 skip (일반 단품)
                    continue

                qr_code_obj = QrCodes(qr_code=part["qr_code"])
                db.add(qr_code_obj)
                await db.flush()

                estimated_wip = EstimatedWips(
                    lazer_cutting_id=cutting.id,
                    qr_id=qr_code_obj.id,
                    manufacturer=target_wip.manufacturer or "POSCO",
                    material=layout.material,
                    thickness=layout.thickness,
                    width=float(part["width"]),
                    length=float(part["height"]),
                    weight=float(part["weight"]),
                )
                db.add(estimated_wip)



def _extract_planned_wip_id_from_qr(qr_code: str | None) -> int | None:
    if not qr_code:
        return None
    match = re.search(r"(?:DEMO-WIP-|QR-DEMO-)([0-9]+)", qr_code)
    return int(match.group(1)) if match else None


def _get_demo_import_job_metadata(cuttings: list[LazerCutting], cut_index: int) -> dict | None:
    if len(cuttings) != len(DEMO_IMPORT_JOBS):
        return None

    expected_minutes = [job["estimatedMinutes"] for job in DEMO_IMPORT_JOBS]
    actual_minutes = [cut.estimated_cutting_time or 0 for cut in cuttings]
    if actual_minutes != expected_minutes:
        return None

    if 0 <= cut_index < len(DEMO_IMPORT_JOBS):
        return DEMO_IMPORT_JOBS[cut_index]

    return None


async def clear_scenario_execution_plan(db: AsyncSession, scenario_id: int) -> None:
    batch_ids = (
        await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))
    ).scalars().all()
    if batch_ids:
        await db.execute(delete(BatchItems).where(BatchItems.batch_id.in_(batch_ids)))
        await db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))

    qr_ids = (
        await db.execute(
            select(EstimatedWips.qr_id)
            .join(LazerCutting, EstimatedWips.lazer_cutting_id == LazerCutting.id)
            .where(LazerCutting.scenario_id == scenario_id, EstimatedWips.qr_id.is_not(None))
        )
    ).scalars().all()
    if qr_ids:
        await db.execute(
            delete(SteelWip).where(
                SteelWip.qr_id.in_(qr_ids),
                SteelWip.status == WipStatus.REGISTERED.value,
            )
        )


async def _resolve_location_ids_by_names(db: AsyncSession, names: list[str]) -> list[int]:
    locations = (
        await db.execute(
            select(Locations).where(Locations.loc_name.in_(names)).order_by(Locations.id.asc())
        )
    ).scalars().all()
    by_name = {loc.loc_name: loc.id for loc in locations if loc.loc_name}
    return [by_name[name] for name in names if name in by_name]


async def _get_picking_destination_ids(db: AsyncSession) -> list[int]:
    preferred = await _resolve_location_ids_by_names(db, PICKING_DESTINATION_NAMES)
    if preferred:
        return preferred

    fallback = (
        await db.execute(select(Locations.id).order_by(Locations.id.asc()).limit(4))
    ).scalars().all()
    return list(fallback)


async def _get_inbound_destination_ids(db: AsyncSession) -> list[int]:
    preferred_names = ["A-1", "A-2", "A-3", "A-4", "B-1", "B-2", "B-3", "C-1", "C-2"]
    preferred = await _resolve_location_ids_by_names(db, preferred_names)
    if preferred:
        return preferred

    stockable = (
        await db.execute(
            select(Locations.id)
            .where(Locations.loc_can_stock == 1)
            .order_by(Locations.id.asc())
        )
    ).scalars().all()
    if stockable:
        return list(stockable)

    fallback = (await db.execute(select(Locations.id).order_by(Locations.id.asc()))).scalars().all()
    return list(fallback)


async def ensure_scenario_execution_plan(
    db: AsyncSession,
    scenario_id: int,
    replace_existing: bool = False,
) -> bool:
    """
    solver가 없어도 field/app에서 사용할 수 있도록
    LANTEK 절단 정보 기반의 임시 배치/작업지시를 생성한다.
    """
    existing_batch_count = (
        await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))
    ).scalars().all()
    if existing_batch_count and not replace_existing:
        return False

    if replace_existing:
        await clear_scenario_execution_plan(db, scenario_id)

    cuttings = (
        await db.execute(
            select(LazerCutting)
            .where(LazerCutting.scenario_id == scenario_id)
            .order_by(LazerCutting.id.asc())
        )
    ).scalars().all()
    if not cuttings:
        return False

    batch_size = 4
    grouped_cuttings = [cuttings[i:i + batch_size] for i in range(0, len(cuttings), batch_size)]
    picking_destinations = await _get_picking_destination_ids(db)
    inbound_destinations = await _get_inbound_destination_ids(db)
    inbound_dest_idx = 0

    for batch_order, group in enumerate(grouped_cuttings, start=1):
        batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
        db.add(batch)
        await db.flush()

        temp_items = []
        current_time = 0
        picking_dest_idx = 0

        for cut in group:
            cut.batch_id = batch.id
            source_wip = await db.get(SteelWip, cut.steel_wip_id) if cut.steel_wip_id else None

            if source_wip and source_wip.location_id:
                picking_dest = None
                if picking_destinations:
                    picking_dest = picking_destinations[picking_dest_idx % len(picking_destinations)]
                    picking_dest_idx += 1
                temp_items.append(
                    {
                        "steel_wip_id": source_wip.id,
                        "action": BatchActionType.PICKING.value,
                        "from": source_wip.location_id,
                        "to": picking_dest,
                        "start_time": current_time,
                        "run_time": 10,
                    }
                )
                current_time += 10

            estimated_wips = (
                await db.execute(
                    select(EstimatedWips)
                    .where(EstimatedWips.lazer_cutting_id == cut.id)
                    .order_by(EstimatedWips.id.asc())
                )
            ).scalars().all()

            for est_wip in estimated_wips:
                realized_wip = None
                if est_wip.qr_id:
                    realized_wip = (
                        await db.execute(select(SteelWip).where(SteelWip.qr_id == est_wip.qr_id))
                    ).scalars().first()
                if realized_wip is None:
                    realized_wip = SteelWip(
                        status=WipStatus.REGISTERED.value,
                        manufacturer=est_wip.manufacturer or (source_wip.manufacturer if source_wip else "POSCO"),
                        material=est_wip.material or (source_wip.material if source_wip else "UNKNOWN"),
                        thickness=est_wip.thickness or 0.0,
                        width=est_wip.width or 0.0,
                        length=est_wip.length or 0.0,
                        weight=est_wip.weight or 0.0,
                        location_id=None,
                        stack_level=None,
                        qr_id=est_wip.qr_id,
                    )
                    db.add(realized_wip)
                    await db.flush()

                inbound_dest = None
                if inbound_destinations:
                    inbound_dest = inbound_destinations[inbound_dest_idx % len(inbound_destinations)]
                    inbound_dest_idx += 1
                temp_items.append(
                    {
                        "steel_wip_id": realized_wip.id,
                        "action": BatchActionType.INBOUND.value,
                        "from": None,
                        "to": inbound_dest,
                        "start_time": current_time + (cut.estimated_cutting_time or 0),
                        "run_time": 5,
                    }
                )

        temp_items.sort(key=lambda item: (item["start_time"], item["action"], item["steel_wip_id"]))
        for item_order, item in enumerate(temp_items, start=1):
            db.add(
                BatchItems(
                    batch_id=batch.id,
                    steel_wip_id=item["steel_wip_id"],
                    batch_item_order=item_order,
                    batch_item_action=item["action"],
                    status=BatchItemStatus.BEFORE_PENDING.value,
                    from_location=item["from"],
                    to_location=item["to"],
                    expected_start_time=item["start_time"],
                    expected_running_time=item["run_time"],
                )
            )

    return True


async def _create_fallback_dummy_lantek_data(db: AsyncSession, scenario_id: int) -> None:
    stmt = select(SteelWip).where(SteelWip.status == WipStatus.IN_STOCK.value).limit(50)
    wip_result = await db.execute(stmt)
    real_wips = wip_result.scalars().all()

    if not real_wips:
        raise ValueError("가용 가능한 재고(IN_STOCK)가 존재하지 않습니다.")

    total_cuttings = 12

    for _ in range(total_cuttings):
        target_wip = random.choice(real_wips)
        if target_wip.status != WipStatus.IN_STOCK.value:
            await db.rollback()
            raise ValueError(f"WIP ID {target_wip.id}는 이미 할당된 재고입니다.")

        cutting_time = random.randint(15, 120)
        cutting = LazerCutting(
            scenario_id=scenario_id,
            status="PENDING",
            priority=random.choice(["LOW", "MIDDLE", "HIGH"]),
            estimated_cutting_time=cutting_time,
            steel_wip_id=target_wip.id,
        )
        db.add(cutting)
        await db.flush()

        for _ in range(random.choice([0, 1, 2])):
            new_width = round(target_wip.width * random.uniform(0.3, 0.7), 1)
            new_length = round(target_wip.length * random.uniform(0.3, 0.7), 1)
            qr_code = QrCodes(qr_code=f"QR-DUMMY-{cutting.id}-{random.randint(1000, 9999)}")
            db.add(qr_code)
            await db.flush()
            db.add(
                EstimatedWips(
                    lazer_cutting_id=cutting.id,
                    manufacturer=target_wip.manufacturer or "POSCO",
                    material=target_wip.material,
                    thickness=target_wip.thickness,
                    width=new_width,
                    length=new_length,
                    weight=_calculate_weight(target_wip.thickness, new_width, new_length),
                    qr_id=qr_code.id,
                )
            )


async def create_dummy_lantek_data(
    db: AsyncSession,
    scenario_id: int,
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> None:
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")

    scenario.status = "DRAFT"

    parsed_layouts: list[ParsedLantekLayout] = []
    if file_bytes:
        try:
            parsed_layouts = _parse_layouts_from_text(_extract_pdf_text(file_bytes))
        except Exception:
            parsed_layouts = []

    if parsed_layouts:
        await _create_parsed_lantek_data(db, scenario, parsed_layouts)
    else:
        await _create_fallback_dummy_lantek_data(db, scenario_id)

    await ensure_scenario_execution_plan(db, scenario_id, replace_existing=True)

    await db.commit()

async def create_lantek_data_from_pdfs(
    db: AsyncSession,
    scenario_id: int,
    files_data: list[dict],  # [{"bytes": bytes, "filename": str}, ...]
) -> None:
    scenario = await db.get(Scenarios, scenario_id)
    if not scenario:
        raise ValueError("시나리오를 찾을 수 없습니다.")

    scenario.status = "DRAFT"

    all_layouts: list[ParsedLantekLayout] = []
    for file_info in files_data:
        try:
            text = _extract_pdf_text(file_info["bytes"])
            layouts = _parse_layouts_from_text(text)
            all_layouts.extend(layouts)
        except Exception:
            pass  # 파싱 실패 PDF는 스킵

    if all_layouts:
        await _create_parsed_lantek_data(db, scenario, all_layouts)
    else:
        await _create_fallback_dummy_lantek_data(db, scenario_id)

    await ensure_scenario_execution_plan(db, scenario_id, replace_existing=True)
    await db.commit()

async def get_lantek_data(db: AsyncSession, scenario_id: int) -> list:
    stmt = (
        select(Scenarios, Projects)
        .join(Projects, Scenarios.project_id == Projects.id)
        .where(Scenarios.id == scenario_id)
    )
    result = await db.execute(stmt)
    row = result.first()

    if not row:
        return []

    scenario, project = row

    cuttings_stmt = (
        select(LazerCutting)
        .where(LazerCutting.scenario_id == scenario.id)
        .order_by(LazerCutting.id.asc())
    )
    cuttings = (await db.execute(cuttings_stmt)).scalars().all()

    lazer_cutting_list = []
    for cut_index, cut in enumerate(cuttings):
        wips_stmt = (
            select(EstimatedWips)
            .where(EstimatedWips.lazer_cutting_id == cut.id)
            .order_by(EstimatedWips.id.asc())
        )
        wips = (await db.execute(wips_stmt)).scalars().all()
        demo_job = _get_demo_import_job_metadata(cuttings, cut_index)

        estimated_wips_mapped: list[LantekEstimatedWip] = []
        for w in wips:
            qr_code = await db.get(QrCodes, w.qr_id) if w.qr_id else None
            qr_code_str = qr_code.qr_code if qr_code else None
 
            estimated_wips_mapped.append(
                LantekEstimatedWip(
                    id=w.id,
                    qrCode=qr_code_str,          # ← plannedWipId 대신 qrCode
                    jobName=None,
                    thickness=w.thickness or 0.0,
                    width=w.width or 0.0,
                    height=w.length or 0.0,
                    weight=w.weight,
                    memo=None,
                )
            )


        total_minutes = cut.estimated_cutting_time or 0
        hours = total_minutes // 60
        mins = total_minutes % 60
        time_str = f"{hours:02d}:{mins:02d}"

        source_wip = await db.get(SteelWip, cut.steel_wip_id) if cut.steel_wip_id else None

        # PDF 파싱값이 있으면 우선 사용, 없으면 source_wip fallback
        if cut.input_width and cut.input_length:
            input_width = float(cut.input_width)
            input_height = float(cut.input_length)
            input_material = cut.input_material or (source_wip.material if source_wip else "SM355A")
            input_thickness = source_wip.thickness if source_wip else 0.0
        else:
            input_width = float(source_wip.width) if source_wip else 0.0
            input_height = float(source_wip.length) if source_wip else 0.0
            input_material = source_wip.material if source_wip else "SM355A"
            input_thickness = source_wip.thickness if source_wip else 0.0

        lazer_cutting_list.append(
            LantekCutting(
                id=cut.id,
                jobName=None,
                ncCode=cut.nc_code,              # ← 추가
                plannedSourceWipId=None,
                estimatedCuttingTime=time_str,
                input=LantekInput(
                    manufacturer="",                                              # ← 공란 (PDF에 없음)
                    material=input_material,
                    thickness=input_thickness,
                    width=input_width,
                    height=input_height,
                    materialType=_determine_material_type(input_width, input_height),
                ),
                estimatedWips=estimated_wips_mapped,
            )
        )

    scenario_data = LantekScenarioData(
        projectId=project.id,
        projectTitle=project.title,
        projectDue=project.project_due,
        scenarioId=scenario.id,
        scenarioTitle=scenario.title,
        scenarioDue=scenario.scenario_due,
        lazerName=(
            scenario.lazer_name.value
            if hasattr(scenario.lazer_name, "value")
            else (scenario.lazer_name or "LAZER1")
        ),
        emergencyOrNot=scenario.emergency_or_not,
        lazerCutting=lazer_cutting_list,
    )

    return [scenario_data]


async def delete_lantek_data(db: AsyncSession, scenario_id: int) -> None:
    cutting_ids_stmt = select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id)
    cutting_ids = (await db.execute(cutting_ids_stmt)).scalars().all()

    if cutting_ids:
        qr_ids_stmt = select(EstimatedWips.qr_id).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids))
        qr_ids = [q for q in (await db.execute(qr_ids_stmt)).scalars().all() if q]

        await db.execute(delete(EstimatedWips).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids)))
        if qr_ids:
            await db.execute(delete(QrCodes).where(QrCodes.id.in_(qr_ids)))
        await db.execute(delete(LazerCutting).where(LazerCutting.scenario_id == scenario_id))

    scenario = await db.get(Scenarios, scenario_id)
    if scenario:
        scenario.status = None

    await db.commit()
