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
    job_name: str | None = None
    planned_source_wip_id: int | None = None
    planned_output_wip_id: int | None = None
    output_width: float | None = None
    output_length: float | None = None


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
    normalized = _normalize_pdf_text(text)
    sections = re.split(r"부품 정보 요약|PART SUMMARY", normalized, flags=re.IGNORECASE)
    layouts: list[ParsedLantekLayout] = []

    for section in sections[1:]:
        slab_match = re.search(
            r"(?:슬랩 사이즈|SLAB SIZE)\s*:\s*([0-9.]+)mm\*([0-9.]+)mm",
            section,
            flags=re.IGNORECASE,
        )
        plate_match = re.search(
            r"(?:판재 크기|PLATE SIZE)\s*:\s*([0-9.]+)mm\*([0-9.]+)mm",
            section,
            flags=re.IGNORECASE,
        )
        time_match = re.search(
            r"(?:단일 가공 시간 시간|CUTTING TIME(?: HOURS)?)\s*:\s*([0-9.]+)",
            section,
            flags=re.IGNORECASE,
        )
        thickness_match = re.search(
            r"(?:판재 두께|THICKNESS)\s*:\s*([0-9.]+)mm",
            section,
            flags=re.IGNORECASE,
        )
        material_match = re.search(
            r"(?:판재 재질|MATERIAL)\s*:\s*([A-Z0-9]+)",
            section,
            flags=re.IGNORECASE,
        )
        layout_match = re.search(
            r"(?:레이아웃|LAYOUT)\s*([0-9]+-[0-9]+/[0-9]+)",
            section,
            flags=re.IGNORECASE,
        )
        job_match = re.search(
            r"(?:작업 이름|JOB NAME)\s*:\s*([A-Za-z0-9_-]+)",
            section,
            flags=re.IGNORECASE,
        )
        source_wip_match = re.search(
            r"(?:원자재 WIP ID|SOURCE WIP ID)\s*:\s*([0-9]+)",
            section,
            flags=re.IGNORECASE,
        )
        output_wip_match = re.search(
            r"(?:발생 재공품 WIP ID|OUTPUT WIP ID)\s*:\s*([0-9]+)",
            section,
            flags=re.IGNORECASE,
        )
        output_size_match = re.search(
            r"(?:발생 재공품 크기|OUTPUT SIZE)\s*:\s*([0-9.]+)mm\*([0-9.]+)mm",
            section,
            flags=re.IGNORECASE,
        )

        if not all([slab_match, plate_match, time_match, thickness_match, material_match]):
            continue

        estimated_minutes = max(1, round(float(time_match.group(1)) * 60))
        layout_name = layout_match.group(1) if layout_match else f"layout-{len(layouts) + 1}"

        layouts.append(
            ParsedLantekLayout(
                layout_name=layout_name,
                slab_width=float(slab_match.group(1)),
                slab_length=float(slab_match.group(2)),
                plate_width=float(plate_match.group(1)),
                plate_length=float(plate_match.group(2)),
                thickness=float(thickness_match.group(1)),
                material=material_match.group(1),
                estimated_minutes=estimated_minutes,
                job_name=job_match.group(1) if job_match else None,
                planned_source_wip_id=int(source_wip_match.group(1)) if source_wip_match else None,
                planned_output_wip_id=int(output_wip_match.group(1)) if output_wip_match else None,
                output_width=float(output_size_match.group(1)) if output_size_match else None,
                output_length=float(output_size_match.group(2)) if output_size_match else None,
            )
        )

    return layouts


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
        )
        db.add(cutting)
        await db.flush()

        if layout.planned_output_wip_id == 0:
            continue

        estimated_width = layout.output_width or layout.slab_width
        estimated_length = layout.output_length or layout.slab_length

        if estimated_width <= 0 or estimated_length <= 0:
            continue

        qr_value = f"LANTEK-{scenario.id}-{index}-{layout.layout_name}"
        if layout.planned_output_wip_id:
            qr_value = f"DEMO-WIP-{layout.planned_output_wip_id}"

        qr_code = QrCodes(qr_code=qr_value)
        db.add(qr_code)
        await db.flush()

        estimated_wip = EstimatedWips(
            lazer_cutting_id=cutting.id,
            qr_id=qr_code.id,
            manufacturer=target_wip.manufacturer or "POSCO",
            material=layout.material,
            thickness=layout.thickness,
            width=estimated_width,
            length=estimated_length,
            weight=_calculate_weight(layout.thickness, estimated_width, estimated_length),
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
            planned_wip_id = _extract_planned_wip_id_from_qr(qr_code.qr_code if qr_code else None)
            memo = None
            if demo_job:
                memo = f"{demo_job['jobName']} 생성 재공품"
                if planned_wip_id:
                    memo += f" · WIP {planned_wip_id}"

            estimated_wips_mapped.append(
                LantekEstimatedWip(
                    id=w.id,
                    plannedWipId=planned_wip_id,
                    jobName=demo_job["jobName"] if demo_job else None,
                    thickness=w.thickness or 0.0,
                    width=w.width or 0.0,
                    height=w.length or 0.0,
                    weight=w.weight,
                    memo=memo,
                )
            )

        total_minutes = cut.estimated_cutting_time or 0
        hours = total_minutes // 60
        mins = total_minutes % 60
        time_str = f"{hours:02d}:{mins:02d}"

        source_wip = await db.get(SteelWip, cut.steel_wip_id) if cut.steel_wip_id else None
        input_manufacturer = source_wip.manufacturer if source_wip else "POSCO"
        input_material = source_wip.material if source_wip else "SM355A"
        input_thickness = source_wip.thickness if source_wip else 0.0
        input_width = source_wip.width if source_wip else 0.0
        input_height = source_wip.length if source_wip else 0.0

        if demo_job:
            input_material = demo_job["material"]
            input_thickness = demo_job["thickness"]
            input_width = demo_job["width"]
            input_height = demo_job["height"]

        lazer_cutting_list.append(
            LantekCutting(
                id=cut.id,
                jobName=demo_job["jobName"] if demo_job else None,
                plannedSourceWipId=demo_job["plannedSourceWipId"] if demo_job else None,
                estimatedCuttingTime=time_str,
                input=LantekInput(
                    manufacturer=input_manufacturer,
                    material=input_material,
                    thickness=input_thickness,
                    width=input_width,
                    height=input_height,
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
