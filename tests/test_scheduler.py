from datetime import date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.caasdy_adapter import _estimate_batch_item_running_time
from app.algorithms.caasdy.data.loader import JobData, WIPData
from app.algorithms.caasdy.env.actions import (
    Action,
    CraneAction,
    ProdAction,
    CRANE_PICKING,
    CRANE_PRE_POSITION,
    CRANE_RESTORE,
    CRANE_TEMP_MOVE,
    PROD_START,
    PROD_NONE,
)
from app.algorithms.caasdy.env.state import MachinePhase, State
from app.algorithms.caasdy.policy.greedy import greedy_policy
from app.algorithms.caasdy.service_interface import log_to_batch_plan
from app.models import Batch, BatchItems, EstimatedWips, LazerCutting, Projects, Scenarios, SteelWip


async def make_project(
    db: AsyncSession,
    title: str = "데모 프로젝트",
    due: date = date(2026, 12, 31),
) -> Projects:
    project = Projects(title=title, project_due=due)
    db.add(project)
    await db.flush()
    return project


async def make_scenario(
    db: AsyncSession,
    project_id: int,
    status: str | None = None,
    title: str = "데모 시나리오-1",
) -> Scenarios:
    scenario = Scenarios(
        title=title,
        status=status,
        scenario_due=date(2026, 12, 31),
        scenario_order=0,
        lazer_name="LAZER1",
        emergency_or_not=False,
        created_at=datetime.now(),
        project_id=project_id,
    )
    db.add(scenario)
    await db.flush()
    return scenario


@pytest.mark.asyncio
async def test_scheduler_main_materializes_demo_solver_result(
    client: AsyncClient,
    db_session: AsyncSession,
):
    project = await make_project(db_session)
    scenario = await make_scenario(db_session, project.id)
    await db_session.commit()

    response = await client.post("/api/scheduler/main", json={"scenario_id": scenario.id})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert "사전 계산된 solver 결과" in body["message"]

    batch_count = len((await db_session.execute(select(Batch).where(Batch.scenario_id == scenario.id))).scalars().all())
    assert batch_count == 2

    items = (
        await db_session.execute(
            select(BatchItems).join(Batch, BatchItems.batch_id == Batch.id).where(Batch.scenario_id == scenario.id)
        )
    ).scalars().all()
    assert len(items) == 12
    assert sum(1 for item in items if item.batch_item_action == "RELOCATE") == 8
    assert sum(1 for item in items if item.batch_item_action == "PICKING") == 2
    assert sum(1 for item in items if item.batch_item_action == "INBOUND") == 2

    cuttings = (
        await db_session.execute(select(LazerCutting).where(LazerCutting.scenario_id == scenario.id))
    ).scalars().all()
    assert len(cuttings) == 3
    assert sum(cut.estimated_cutting_time or 0 for cut in cuttings) == 255

    estimated_wips = (
        await db_session.execute(
            select(EstimatedWips).join(LazerCutting, EstimatedWips.lazer_cutting_id == LazerCutting.id).where(
                LazerCutting.scenario_id == scenario.id
            )
        )
    ).scalars().all()
    assert len(estimated_wips) == 2

    generated_wips = (
        await db_session.execute(select(SteelWip).where(SteelWip.id.in_([103, 104])))
    ).scalars().all()
    assert len(generated_wips) == 2
    assert all(wip.status == "REGISTERED" for wip in generated_wips)

    input_wips = (
        await db_session.execute(select(SteelWip).where(SteelWip.id.in_([17, 28, 73, 99])))
    ).scalars().all()
    assert len(input_wips) == 4
    assert all(wip.qr_id is not None for wip in input_wips)
    assert any(wip.width == 950.0 and wip.length == 2530.0 for wip in input_wips if wip.id == 28)


@pytest.mark.asyncio
async def test_scheduler_main_demo_result_is_visible_in_scenario_result(
    client: AsyncClient,
    db_session: AsyncSession,
):
    project = await make_project(db_session, title="데모 프로젝트 B")
    scenario = await make_scenario(db_session, project.id, title="데모 시나리오-B")
    await db_session.commit()

    scheduler_response = await client.post("/api/scheduler/main", json={"scenario_id": scenario.id})
    assert scheduler_response.status_code == 200

    result_response = await client.get(f"/api/scenario/{scenario.id}")
    assert result_response.status_code == 200

    data = result_response.json()["data"]
    assert len(data) == 1
    scenario_result = data[0]
    assert scenario_result["totalCuttingTime"] == 268
    assert scenario_result["totalWipNum"] == 2
    assert scenario_result["projectDue"] == "2026-12-31"
    assert scenario_result["orderedAt"] is not None
    assert scenario_result["numInputWip"] == 2
    assert scenario_result["emergencyOrNot"] is False
    assert scenario_result["totalMoveNum"] == 8
    assert scenario_result["solverSummary"]["status"] == "TIME_LIMIT"
    assert scenario_result["solverSummary"]["objective"] == 8
    assert scenario_result["solverSummary"]["mipGap"] == 87.5
    assert scenario_result["solverSummary"]["solveSeconds"] == 600.1
    assert len(scenario_result["jobSchedule"]) == 3
    assert scenario_result["jobSchedule"][1]["jobName"] == "Job2"
    assert scenario_result["jobSchedule"][1]["pickWips"] == [28]
    assert scenario_result["jobSchedule"][1]["outputWips"] == [103]
    assert len(scenario_result["craneSchedule"]) == 12
    assert scenario_result["craneSchedule"][0]["action"] == "RELOCATE"
    assert scenario_result["craneSchedule"][0]["actionLabel"] == "재배치"
    assert scenario_result["craneSchedule"][0]["batchItemId"] is not None
    assert scenario_result["craneSchedule"][0]["batchId"] is not None
    assert scenario_result["craneSchedule"][0]["batchItemOrder"] is not None
    assert scenario_result["craneSchedule"][0]["expectedStartMinute"] == 0.0
    assert scenario_result["craneSchedule"][0]["expectedDurationMinutes"] == 5.0
    assert scenario_result["craneSchedule"][0]["expectedEndMinute"] == 5.0
    assert scenario_result["craneSchedule"][-1]["steelWipId"] == 103
    assert all(item["qrCode"] for item in scenario_result["craneSchedule"])
    assert all(item["thickness"] is not None for item in scenario_result["craneSchedule"])
    assert all(item["width"] is not None for item in scenario_result["craneSchedule"])
    assert all(item["length"] is not None for item in scenario_result["craneSchedule"])
    assert any(item["ncCode"] for item in scenario_result["craneSchedule"])
    assert any(
        item["steelWipId"] == 78
        and item["thickness"] == 12.0
        and item["width"] == 2438.0
        and item["length"] == 6096.0
        for item in scenario_result["craneSchedule"]
    )
    assert any(
        item["steelWipId"] == 37
        and item["thickness"] == 16.0
        and item["width"] == 715.0
        and item["length"] == 1890.0
        for item in scenario_result["craneSchedule"]
    )
    assert any(
        item["steelWipId"] == 104
        and item["thickness"] == 20.0
        and item["width"] == 1190.0
        and item["length"] == 570.0
        for item in scenario_result["craneSchedule"]
    )
    assert len(scenario_result["batchItems"]) == 12
    assert any(item["steelWipId"] == 103 and item["batchItemAction"] == "적재" for item in scenario_result["batchItems"])
    assert any(item["steelWipId"] == 104 and item["batchItemAction"] == "적재" for item in scenario_result["batchItems"])


def test_estimate_batch_item_running_time_uses_buffer_proxy_and_machine_times():
    inter_times = {
        ("A-1", "B-6"): 2.3,
        ("B-6", "A-1"): 2.3,
        ("B-6", "A-2"): 1.2,
        ("A-2", "B-6"): 1.2,
        ("A-3", "B-1"): 0.4,
        ("B-1", "A-3"): 0.4,
    }
    machine_times = {
        "A-3": 4.2,
        "B-1": 3.6,
    }

    temp_move_minutes = _estimate_batch_item_running_time(
        action="TEMP_MOVE",
        from_loc_name="A-1",
        to_loc_name="BUF-1",
        inter_times=inter_times,
        machine_times=machine_times,
    )
    restore_minutes = _estimate_batch_item_running_time(
        action="RESTORE",
        from_loc_name="BUF-1",
        to_loc_name="A-2",
        inter_times=inter_times,
        machine_times=machine_times,
    )
    picking_minutes = _estimate_batch_item_running_time(
        action="PICKING",
        from_loc_name="A-3",
        to_loc_name="S4-1",
        inter_times=inter_times,
        machine_times=machine_times,
    )
    inbound_minutes = _estimate_batch_item_running_time(
        action="INBOUND",
        from_loc_name=None,
        to_loc_name="B-1",
        inter_times=inter_times,
        machine_times=machine_times,
    )
    relocate_minutes = _estimate_batch_item_running_time(
        action="RELOCATE",
        from_loc_name="A-3",
        to_loc_name="B-1",
        inter_times=inter_times,
        machine_times=machine_times,
    )

    assert temp_move_minutes == 3
    assert restore_minutes == 2
    assert picking_minutes == 5
    assert inbound_minutes == 4
    assert relocate_minutes == 1


def test_log_to_batch_plan_restores_to_origin_when_no_future_unload_remains():
    location_map = {
        "A-1": 1,
        "A-2": 2,
    }
    buffer_location_map = {"BUF-1": 15}
    s4_location_map = {"S4-1": 11, "S4-2": 12, "S4-3": 13, "S4-4": 14}

    log = [
        {
            "action": Action(
                crane=CraneAction(CRANE_TEMP_MOVE, wip_id=101, src_stack=1),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 1,
            "clock": 0.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_PRE_POSITION, wip_id=101, dst_stack=2),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 2,
            "clock": 5.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_PICKING, wip_id=202, src_stack=2, job_id=77),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 3,
            "clock": 8.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_TEMP_MOVE, wip_id=303, src_stack=2),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 4,
            "clock": 12.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_RESTORE, wip_id=303, dst_stack=1),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 5,
            "clock": 14.0,
        },
    ]

    items = log_to_batch_plan(
        log=log,
        location_map=location_map,
        buffer_location_map=buffer_location_map,
        s4_location_map=s4_location_map,
    )

    assert items[0]["action"] == "TEMP_MOVE"
    assert items[0]["from_location_id"] == 1
    assert items[0]["to_location_id"] == 15

    assert items[1]["action"] == "RESTORE"
    assert items[1]["from_location_id"] == 15
    assert items[1]["to_location_id"] == 1

    assert items[4]["action"] == "RESTORE"
    assert items[4]["from_location_id"] == 15
    assert items[4]["to_location_id"] == 2


def test_log_to_batch_plan_keeps_buffer_exit_relocate_when_origin_stack_needed_later():
    location_map = {
        "A-4": 4,
        "B-2": 6,
    }
    buffer_location_map = {"BUF-1": 15}
    s4_location_map = {"S4-1": 11, "S4-2": 12, "S4-3": 13, "S4-4": 14}

    log = [
        {
            "action": Action(
                crane=CraneAction(CRANE_TEMP_MOVE, wip_id=74, src_stack=4),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 1,
            "clock": 0.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_PRE_POSITION, wip_id=74, dst_stack=6),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 2,
            "clock": 4.0,
        },
        {
            "action": Action(
                crane=CraneAction(CRANE_TEMP_MOVE, wip_id=56, src_stack=4),
                prod=ProdAction(PROD_NONE),
            ),
            "step": 3,
            "clock": 8.0,
        },
    ]

    items = log_to_batch_plan(
        log=log,
        location_map=location_map,
        buffer_location_map=buffer_location_map,
        s4_location_map=s4_location_map,
    )

    assert items[1]["action"] == "RELOCATE"
    assert items[1]["from_location_id"] == 15
    assert items[1]["to_location_id"] == 6


def test_greedy_policy_restores_buffer_before_starting_process():
    state = State(
        stacks={1: [11], 2: []},
        crane_loc="A-1",
        buffer_wips=frozenset({99}),
        buffer_cap=1,
        phase=MachinePhase.LOADING,
        K_mach=frozenset({11}),
        j_mach=501,
        u_short=100.0,
        u_long=200.0,
        eta=0.0,
        O_wait=frozenset(),
        clock=0.0,
        Q_rem=frozenset({501}),
        Q_done=frozenset(),
    )
    wip_data = {
        11: WIPData(11, 1, 1, 100.0, 200.0, 10.0, "SS275", "10*100*200"),
        99: WIPData(99, 2, 1, 120.0, 240.0, 12.0, "SS275", "12*120*240"),
    }
    job_data = {
        501: JobData(
            job_id=501,
            input_wip_id=11,
            grade="SS275",
            spec="10*100*200",
            batch_count=1,
            process_time=15.0,
            cap_short=100.0,
            cap_long=200.0,
            thickness=10.0,
            short_side=100.0,
            long_side=200.0,
            generates_output=False,
            output_wip_id=None,
            has_external_input=False,
        ),
    }

    action = greedy_policy(state, wip_data, job_data)

    assert action.crane.type == CRANE_RESTORE
    assert action.crane.wip_id == 99
    assert action.prod.type == PROD_NONE
