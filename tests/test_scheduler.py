from datetime import date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    assert scenario_result["craneSchedule"][-1]["steelWipId"] == 103
    assert all(item["qrCode"] for item in scenario_result["craneSchedule"])
    assert all(item["thickness"] is not None for item in scenario_result["craneSchedule"])
    assert all(item["width"] is not None for item in scenario_result["craneSchedule"])
    assert all(item["length"] is not None for item in scenario_result["craneSchedule"])
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
