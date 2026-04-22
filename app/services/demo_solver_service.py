from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchItems, EstimatedWips, LazerCutting, QrCodes, Scenarios, SteelWip
from app.schemas.batch_item import BatchItemStatus
from app.schemas.enums import BatchActionType, WipStatus


STEEL_DENSITY = 7.85 / 1_000_000
DEMO_LAZER_LOCATION_ID = 10
DEMO_INVENTORY_DATA_PATH = Path("/Users/choibk/Claude/졸업프로젝트_연구파트/input_data_v2/01_inventory_data.csv")
DEMO_PRODUCTION_PLAN_PATH = Path("/Users/choibk/Claude/졸업프로젝트_연구파트/input_data_v2/02_production_plan.csv")


@dataclass(frozen=True)
class DemoWipSpec:
    id: int
    material: str
    thickness: float
    width: float
    length: float
    manufacturer: str
    location_id: int | None
    stack_level: int | None
    status: str


@dataclass(frozen=True)
class DemoTaskSpec:
    action: str
    steel_wip_id: int
    from_location: int | None
    to_location: int | None
    expected_start_time: int
    expected_running_time: int


@dataclass(frozen=True)
class DemoCuttingSpec:
    steel_wip_id: int | None
    estimated_cutting_time: int
    batch_order: int | None
    output_wip_id: int | None


@dataclass(frozen=True)
class DemoJobScheduleSpec:
    job_name: str
    sequence: int
    start_minute: float
    end_minute: float
    pick_wips: list[int]
    output_wips: list[int]


@dataclass(frozen=True)
class DemoCraneScheduleSpec:
    order: int
    action: str
    steel_wip_id: int
    from_location: str
    to_location: str
    event_minute: float
    move_type: str | None = None


@dataclass(frozen=True)
class DemoProductionPlanSpec:
    steel_wip_id: int
    material: str
    thickness: float
    width: float
    length: float


DEMO_INPUT_WIPS = [
    DemoWipSpec(17, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 24, WipStatus.IN_STOCK.value),
    DemoWipSpec(19, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 19, WipStatus.IN_STOCK.value),
    DemoWipSpec(22, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 20, WipStatus.IN_STOCK.value),
    DemoWipSpec(28, "SM355A", 12.0, 950.0, 2530.0, "POSCO", 3, 17, WipStatus.IN_STOCK.value),
    DemoWipSpec(37, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 23, WipStatus.IN_STOCK.value),
    DemoWipSpec(73, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 21, WipStatus.IN_STOCK.value),
    DemoWipSpec(78, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 25, WipStatus.IN_STOCK.value),
    DemoWipSpec(90, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 22, WipStatus.IN_STOCK.value),
    DemoWipSpec(97, "SM355A", 12.0, 2438.0, 6096.0, "POSCO", 3, 18, WipStatus.IN_STOCK.value),
    DemoWipSpec(99, "SS275", 20.0, 570.0, 2450.0, "HYUNDAI", 4, 25, WipStatus.IN_STOCK.value),
]

DEMO_OUTPUT_WIPS = [
    DemoWipSpec(103, "SM355A", 12.0, 950.0, 1690.0, "POSCO", None, None, WipStatus.REGISTERED.value),
    DemoWipSpec(104, "SS275", 20.0, 1190.0, 570.0, "HYUNDAI", None, None, WipStatus.REGISTERED.value),
]

DEMO_BATCHES: list[list[DemoTaskSpec]] = [
    [
        DemoTaskSpec(BatchActionType.RELOCATE.value, 78, 3, 2, 1, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 17, 3, 2, 2, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 37, 3, 2, 3, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 90, 3, 1, 4, 1),
        DemoTaskSpec(BatchActionType.PICKING.value, 99, 4, DEMO_LAZER_LOCATION_ID, 7, 4),
        DemoTaskSpec(BatchActionType.INBOUND.value, 104, None, 1, 14, 5),
    ],
    [
        DemoTaskSpec(BatchActionType.RELOCATE.value, 73, 3, 4, 8, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 22, 3, 4, 9, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 19, 3, 4, 10, 1),
        DemoTaskSpec(BatchActionType.RELOCATE.value, 97, 3, 4, 11, 1),
        DemoTaskSpec(BatchActionType.PICKING.value, 28, 3, DEMO_LAZER_LOCATION_ID, 17, 10),
        DemoTaskSpec(BatchActionType.INBOUND.value, 103, None, 1, 400, 5),
    ],
]

DEMO_CUTTINGS = [
    DemoCuttingSpec(steel_wip_id=None, estimated_cutting_time=241, batch_order=None, output_wip_id=None),
    DemoCuttingSpec(steel_wip_id=99, estimated_cutting_time=4, batch_order=1, output_wip_id=104),
    DemoCuttingSpec(steel_wip_id=28, estimated_cutting_time=10, batch_order=2, output_wip_id=103),
]

DEMO_SOLVER_SUMMARY = {
    "status": "TIME_LIMIT",
    "objective": 8,
    "mipGap": 87.5,
    "solutions": 1,
    "solveSeconds": 600.1,
    "makespanMinutes": 267.6,
}

DEMO_JOB_SCHEDULE = [
    DemoJobScheduleSpec("Job1", 3, 26.8, 267.6, [], []),
    DemoJobScheduleSpec("Job2", 2, 17.1, 26.8, [28], [103]),
    DemoJobScheduleSpec("Job3", 1, 7.2, 11.1, [99], [104]),
]

DEMO_CRANE_SCHEDULE = [
    DemoCraneScheduleSpec(1, "RELOCATE", 78, "3", "2", 0.77, "RS"),
    DemoCraneScheduleSpec(2, "RELOCATE", 17, "3", "2", 1.54, "RS"),
    DemoCraneScheduleSpec(3, "RELOCATE", 37, "3", "2", 2.31, "RS"),
    DemoCraneScheduleSpec(4, "RELOCATE", 90, "3", "1", 3.53, "RS"),
    DemoCraneScheduleSpec(5, "PICK", 99, "4", "설비", 7.19, None),
    DemoCraneScheduleSpec(6, "RELOCATE", 73, "3", "4", 7.98, "PM"),
    DemoCraneScheduleSpec(7, "RELOCATE", 22, "3", "4", 8.76, "PM"),
    DemoCraneScheduleSpec(8, "RELOCATE", 19, "3", "4", 10.31, "PM"),
    DemoCraneScheduleSpec(12, "RELOCATE", 97, "3", "4", 11.09, "PM"),
    DemoCraneScheduleSpec(22, "STORE", 104, "설비", "1", 13.75, None),
    DemoCraneScheduleSpec(23, "PICK", 28, "3", "설비", 17.13, None),
    DemoCraneScheduleSpec(24, "STORE", 103, "설비", "1", 400.0, None),
]


def _parse_size_text(size_text: str) -> tuple[float, float, float] | None:
    parts = [part.strip() for part in str(size_text or "").split("*")]
    if len(parts) != 3:
        return None
    try:
        thickness, width, length = (float(part) for part in parts)
    except ValueError:
        return None
    return thickness, width, length


@lru_cache(maxsize=1)
def load_demo_inventory_location_specs() -> dict[int, tuple[int | None, int | None]]:
    specs: dict[int, tuple[int | None, int | None]] = {}

    if not DEMO_INVENTORY_DATA_PATH.exists():
        return specs

    with DEMO_INVENTORY_DATA_PATH.open(newline="", encoding="cp949") as file:
        reader = csv.DictReader(file)
        for row in reader:
            mat_id_text = (row.get("aMatID") or "").strip()
            if not mat_id_text:
                continue
            try:
                mat_id = int(mat_id_text)
            except ValueError:
                continue
            location_id = int(row["aLocationId"]) if (row.get("aLocationId") or "").strip() else None
            stack_level = int(row["aStackLevel"]) if (row.get("aStackLevel") or "").strip() else None
            specs[mat_id] = (location_id, stack_level)

    return specs


@lru_cache(maxsize=1)
def load_demo_production_plan_specs() -> dict[int, DemoProductionPlanSpec]:
    fallback_specs = [*DEMO_INPUT_WIPS, *DEMO_OUTPUT_WIPS]
    specs: dict[int, DemoProductionPlanSpec] = {
        spec.id: DemoProductionPlanSpec(
            steel_wip_id=spec.id,
            material=spec.material,
            thickness=spec.thickness,
            width=spec.width,
            length=spec.length,
        )
        for spec in fallback_specs
    }

    if DEMO_PRODUCTION_PLAN_PATH.exists():
        with DEMO_PRODUCTION_PLAN_PATH.open(newline="", encoding="cp949") as file:
            reader = csv.DictReader(file)
            for row in reader:
                wip_id_text = (row.get("대응재공품ID") or "").strip()
                if not wip_id_text or wip_id_text == "0":
                    continue
                parsed_size = _parse_size_text(row.get("판재사이즈") or "")
                if not parsed_size:
                    continue
                thickness, width, length = parsed_size
                wip_id = int(wip_id_text)
                specs[wip_id] = DemoProductionPlanSpec(
                    steel_wip_id=wip_id,
                    material=(row.get("재질") or "").strip() or "알수없음",
                    thickness=thickness,
                    width=width,
                    length=length,
                )

    return specs


@lru_cache(maxsize=1)
def load_demo_seed_input_wips() -> tuple[DemoWipSpec, ...]:
    production_specs = load_demo_production_plan_specs()
    inventory_specs = load_demo_inventory_location_specs()

    seeded_specs: list[DemoWipSpec] = []
    for spec in DEMO_INPUT_WIPS:
        production_spec = production_specs.get(spec.id)
        location_id, stack_level = inventory_specs.get(spec.id, (spec.location_id, spec.stack_level))
        seeded_specs.append(
            DemoWipSpec(
                id=spec.id,
                material=production_spec.material if production_spec else spec.material,
                thickness=production_spec.thickness if production_spec else spec.thickness,
                width=production_spec.width if production_spec else spec.width,
                length=production_spec.length if production_spec else spec.length,
                manufacturer=spec.manufacturer,
                location_id=location_id,
                stack_level=stack_level,
                status=spec.status,
            )
        )

    return tuple(seeded_specs)


@lru_cache(maxsize=1)
def load_demo_seed_output_wips() -> tuple[DemoWipSpec, ...]:
    production_specs = load_demo_production_plan_specs()

    seeded_specs: list[DemoWipSpec] = []
    for spec in DEMO_OUTPUT_WIPS:
        production_spec = production_specs.get(spec.id)
        seeded_specs.append(
            DemoWipSpec(
                id=spec.id,
                material=production_spec.material if production_spec else spec.material,
                thickness=production_spec.thickness if production_spec else spec.thickness,
                width=production_spec.width if production_spec else spec.width,
                length=production_spec.length if production_spec else spec.length,
                manufacturer=spec.manufacturer,
                location_id=spec.location_id,
                stack_level=spec.stack_level,
                status=spec.status,
            )
        )

    return tuple(seeded_specs)


def _calculate_weight(thickness: float, width: float, length: float) -> float:
    return round(thickness * width * length * STEEL_DENSITY, 1)


def matches_demo_solver_result(batch_items: list[BatchItems], cuttings: list[LazerCutting]) -> bool:
    demo_tasks = sorted(
        [task for batch in DEMO_BATCHES for task in batch],
        key=lambda task: (task.expected_start_time, task.steel_wip_id),
    )
    expected_task_signature = [
        (task.action, task.steel_wip_id, task.from_location, task.to_location, task.expected_start_time)
        for task in demo_tasks
    ]
    actual_task_signature = [
        (
            item.batch_item_action.value if hasattr(item.batch_item_action, "value") else str(item.batch_item_action),
            item.steel_wip_id,
            item.from_location,
            item.to_location,
            item.expected_start_time,
        )
        for item in sorted(batch_items, key=lambda item: (item.expected_start_time or 0, item.batch_item_order or 0))
    ]
    expected_cutting_signature = [
        (cutting.steel_wip_id, cutting.estimated_cutting_time)
        for cutting in DEMO_CUTTINGS
    ]
    actual_cutting_signature = [
        (
            cut.steel_wip_id,
            cut.estimated_cutting_time,
        )
        for cut in sorted(cuttings, key=lambda cut: cut.id)
    ]

    return actual_task_signature == expected_task_signature and actual_cutting_signature == expected_cutting_signature


async def _get_or_create_qr(db: AsyncSession, code: str) -> QrCodes:
    existing = (
        await db.execute(select(QrCodes).where(QrCodes.qr_code == code))
    ).scalars().first()
    if existing:
        return existing

    qr = QrCodes(qr_code=code)
    db.add(qr)
    await db.flush()
    return qr


async def _upsert_demo_wip(
    db: AsyncSession,
    spec: DemoWipSpec,
    qr_id: int | None = None,
) -> SteelWip:
    wip = await db.get(SteelWip, spec.id)
    if wip is None:
        wip = SteelWip(
            id=spec.id,
            status=spec.status,
            material=spec.material,
            thickness=spec.thickness,
            width=spec.width,
            length=spec.length,
            weight=_calculate_weight(spec.thickness, spec.width, spec.length),
            manufacturer=spec.manufacturer,
            location_id=spec.location_id,
            stack_level=spec.stack_level,
            qr_id=qr_id,
        )
        db.add(wip)
        await db.flush()
        return wip

    wip.status = spec.status
    wip.material = spec.material
    wip.thickness = spec.thickness
    wip.width = spec.width
    wip.length = spec.length
    wip.weight = _calculate_weight(spec.thickness, spec.width, spec.length)
    wip.manufacturer = spec.manufacturer
    wip.location_id = spec.location_id
    wip.stack_level = spec.stack_level
    wip.qr_id = qr_id
    await db.flush()
    return wip


async def clear_demo_solver_result(db: AsyncSession, scenario_id: int) -> None:
    qr_ids: list[int] = []
    cutting_ids = (
        await db.execute(select(LazerCutting.id).where(LazerCutting.scenario_id == scenario_id))
    ).scalars().all()
    if cutting_ids:
        qr_ids = (
            await db.execute(
                select(EstimatedWips.qr_id).where(
                    EstimatedWips.lazer_cutting_id.in_(cutting_ids),
                    EstimatedWips.qr_id.is_not(None),
                )
            )
        ).scalars().all()

        await db.execute(delete(EstimatedWips).where(EstimatedWips.lazer_cutting_id.in_(cutting_ids)))
        await db.execute(delete(LazerCutting).where(LazerCutting.id.in_(cutting_ids)))

    batch_ids = (
        await db.execute(select(Batch.id).where(Batch.scenario_id == scenario_id))
    ).scalars().all()
    if batch_ids:
        await db.execute(delete(BatchItems).where(BatchItems.batch_id.in_(batch_ids)))
        await db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))

    if qr_ids:
        await db.execute(
            delete(SteelWip).where(
                SteelWip.qr_id.in_(qr_ids),
                SteelWip.status == WipStatus.REGISTERED.value,
            )
        )
        await db.execute(delete(QrCodes).where(QrCodes.id.in_(qr_ids)))


async def materialize_demo_solver_result(db: AsyncSession, scenario_id: int) -> dict[str, int]:
    """
    실제 Gurobi solver 대신, 사전 계산된 md 결과를 DB에 반영한다.
    시연 시에는 이 경로를 사용해 즉시 일관된 시나리오 결과를 제공한다.
    """
    scenario = await db.get(Scenarios, scenario_id)
    if scenario is None:
        raise ValueError("시나리오를 찾을 수 없습니다.")

    await clear_demo_solver_result(db, scenario_id)
    scenario.status = "DRAFT"

    for spec in load_demo_seed_input_wips():
        qr = await _get_or_create_qr(db, f"QR-WIP-{spec.id:03d}")
        await _upsert_demo_wip(db, spec, qr.id)

    output_wips_by_id: dict[int, SteelWip] = {}
    for spec in DEMO_OUTPUT_WIPS:
        qr = await _get_or_create_qr(db, f"QR-DEMO-{spec.id}")
        output_wips_by_id[spec.id] = await _upsert_demo_wip(db, spec, qr.id)

    batches_by_order: dict[int, Batch] = {}
    for batch_order, task_specs in enumerate(DEMO_BATCHES, start=1):
        batch = Batch(scenario_id=scenario_id, batch_order=batch_order)
        db.add(batch)
        await db.flush()
        batches_by_order[batch_order] = batch

        for item_order, task in enumerate(task_specs, start=1):
            db.add(
                BatchItems(
                    batch_id=batch.id,
                    batch_item_action=task.action,
                    status=BatchItemStatus.BEFORE_PENDING.value,
                    steel_wip_id=task.steel_wip_id,
                    batch_item_order=item_order,
                    from_location=task.from_location,
                    to_location=task.to_location,
                    expected_start_time=task.expected_start_time,
                    expected_running_time=task.expected_running_time,
                )
            )

    for cutting_spec in DEMO_CUTTINGS:
        batch = batches_by_order.get(cutting_spec.batch_order) if cutting_spec.batch_order else None
        cutting = LazerCutting(
            scenario_id=scenario_id,
            batch_id=batch.id if batch else None,
            steel_wip_id=cutting_spec.steel_wip_id,
            estimated_cutting_time=cutting_spec.estimated_cutting_time,
            status="PENDING",
            priority="LOW",
        )
        db.add(cutting)
        await db.flush()

        if cutting_spec.output_wip_id is None:
            continue

        output_wip = output_wips_by_id[cutting_spec.output_wip_id]
        db.add(
            EstimatedWips(
                lazer_cutting_id=cutting.id,
                qr_id=output_wip.qr_id,
                manufacturer=output_wip.manufacturer,
                material=output_wip.material,
                thickness=output_wip.thickness,
                width=output_wip.width,
                length=output_wip.length,
                weight=output_wip.weight,
            )
        )

    await db.flush()

    return {
        "batchCount": len(DEMO_BATCHES),
        "taskCount": sum(len(batch) for batch in DEMO_BATCHES),
        "cuttingCount": len(DEMO_CUTTINGS),
        "generatedWipCount": len(DEMO_OUTPUT_WIPS),
    }
