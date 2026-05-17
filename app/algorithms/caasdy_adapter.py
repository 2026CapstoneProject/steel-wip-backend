"""
app/algorithms/caasdy_adapter.py
────────────────────────────────────────────────────────────────────────────
CAASDy 솔버 ↔ 서비스 DB 비동기 브리지

DB 조회/저장을 담당하며, 순수 연산은 caasdy/service_interface.py 에 위임한다.

공개 함수
─────────
  run_caasdy_for_scenario(db, scenario_id) → bool
    시나리오에 대한 CAASDy 솔버를 실행하고 BatchItems를 저장한다.
    성공 시 True, 데이터 없음/오류 시 False 반환.

  ensure_buffer_location(db) → int
    BUF-1 위치가 없으면 생성하고 locations.id를 반환한다.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Batch, BatchItems, BatchItemsBatchItemAction,
    BatchItemsStatus, Locations, LazerCutting, EstimatedWips, RawMaterialSpecs, SteelWip,
    SteelWipStatus,
)

logger = logging.getLogger(__name__)

# ── caasdy 패키지 경로 ────────────────────────────────────────────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "caasdy", "data")   # distance_matrix CSV 위치
_INBOUND_NODE_ORDER = [
    "A-1", "A-2", "A-3", "A-4",
    "B-1", "B-2", "B-3", "B-4", "B-5", "B-6",
]
def _same_dimension_pair(
    width_a: float,
    length_a: float,
    width_b: float,
    length_b: float,
) -> bool:
    return {
        round(float(width_a)),
        round(float(length_a)),
    } == {
        round(float(width_b)),
        round(float(length_b)),
    }


async def _is_allowed_raw_material_spec(
    db: AsyncSession,
    material: str | None,
    thickness: float | None,
    width: float | None,
    length: float | None,
) -> bool:
    if (
        material is None
        or thickness is None
        or width is None
        or length is None
    ):
        return False

    specs = (
        await db.execute(
            select(RawMaterialSpecs).where(
                RawMaterialSpecs.is_active == 1,
                RawMaterialSpecs.material == material,
                RawMaterialSpecs.thickness == thickness,
            )
        )
    ).scalars().all()
    return any(
        _same_dimension_pair(spec.width, spec.length, width, length)
        for spec in specs
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 공개 진입점
# ═══════════════════════════════════════════════════════════════════════════════

async def run_caasdy_for_scenario(
    db:          AsyncSession,
    scenario_id: int,
) -> bool:
    """
    시나리오에 대한 CAASDy 솔버를 실행하고 결과를 Batch/BatchItems로 저장한다.

    단일 Batch(batch_order=1)에 모든 스텝을 순서대로 저장한다.
    (배치 분할이 필요하다면 batch_order를 확장할 수 있음)

    Returns
    -------
    True  : 저장 성공
    False : 데이터 없음 또는 솔버 오류
    """
    # ── 1. DB 조회 ────────────────────────────────────────────────────────────
    cutting_records = await _query_cutting_records(db, scenario_id)
    if not cutting_records:
        logger.warning("시나리오 %d: LazerCutting 레코드 없음 → 스킵", scenario_id)
        return False

    wip_records = await _query_wip_records(db, cutting_records)
    if not wip_records:
        logger.warning("시나리오 %d: 야드 WIP 없음 → 스킵", scenario_id)
        return False

    # ── 2. 위치 매핑 구성 ─────────────────────────────────────────────────────
    location_map        = await _get_yard_location_map(db)
    buffer_location_map = await _get_buffer_location_map(db)
    s4_location_map     = await _get_s4_location_map(db)

    if not s4_location_map:
        logger.error("S4-1~S4-4 위치 없음 — locations seed 를 확인하세요.")
        return False

    # ── 3. DIDPPy 가용성 사전 확인 ───────────────────────────────────────────
    try:
        from app.algorithms.caasdy.didp.solver import is_available as _didp_ok
        if not _didp_ok():
            logger.error(
                "[CAASDy] DIDPPy가 설치되어 있지 않습니다. "
                "pip install didppy>=0.8.0 을 실행하세요."
            )
            return False
        logger.info("[CAASDy] DIDPPy 확인됨 — 솔버 실행 시작")
    except Exception as _e:
        logger.error("[CAASDy] didp.solver import 실패: %s", _e)
        return False

    # ── 4. 솔버 실행 (동기 함수를 executor에서 실행) ──────────────────────────
    try:
        log = await asyncio.get_event_loop().run_in_executor(
            None,
            _run_solver_sync,
            wip_records,
            cutting_records,
        )
    except ImportError as e:
        logger.error("[CAASDy] ImportError (import 경로 문제): %s", e, exc_info=True)
        return False
    except Exception as e:
        logger.error("[CAASDy] 솔버 실행 중 오류: %s", e, exc_info=True)
        return False

    if not log:
        logger.warning("시나리오 %d: 솔버가 빈 로그 반환", scenario_id)
        return False

    # ── 4. log → BatchItem dict 변환 ─────────────────────────────────────────
    from app.algorithms.caasdy.service_interface import log_to_batch_plan

    batch_plan = log_to_batch_plan(
        log                 = log,
        location_map        = location_map,
        buffer_location_map = buffer_location_map,
        s4_location_map     = s4_location_map,
        direct_start_wip_map = {
            record["id"]: record.get("source_steel_wip_id")
            for record in cutting_records
            if record.get("source_steel_wip_id")
            and not record.get("steel_wip_id")
        },
    )

    _append_inbound_items(
        batch_plan=batch_plan,
        cutting_records=cutting_records,
        location_map=location_map,
        log=log,
    )

    if not batch_plan:
        logger.warning("시나리오 %d: batch_plan 항목 없음", scenario_id)
        return False

    # ── 5. DB 저장 ────────────────────────────────────────────────────────────
    await _save_batch_plan(db, scenario_id, batch_plan)

    logger.info(
        "시나리오 %d: CAASDy BatchItems %d건 저장 완료",
        scenario_id, len(batch_plan),
    )
    return True


async def ensure_buffer_location(db: AsyncSession) -> int:
    """
    BUF-1 위치가 없으면 생성하고 locations.id를 반환한다.
    """
    row = (
        await db.execute(
            select(Locations).where(Locations.loc_name == "BUF-1")
        )
    ).scalars().first()
    if row:
        return row.id

    buf = Locations(loc_name="BUF-1", loc_can_stock=0, loc_stack_height=2)
    db.add(buf)
    await db.flush()
    logger.info("BUF-1 위치 생성: id=%d", buf.id)
    return buf.id


# ═══════════════════════════════════════════════════════════════════════════════
# 내부 헬퍼 — DB 조회
# ═══════════════════════════════════════════════════════════════════════════════

async def _query_cutting_records(
    db:          AsyncSession,
    scenario_id: int,
) -> List[dict]:
    """시나리오의 LazerCutting + EstimatedWips 조회."""
    cuts = (
        await db.execute(
            select(LazerCutting)
            .where(LazerCutting.scenario_id == scenario_id)
            .order_by(LazerCutting.id.asc())
        )
    ).scalars().all()

    records: List[dict] = []
    for cut in cuts:
        # EstimatedWips 중 첫 번째 → output_placeholder
        est = (
            await db.execute(
                select(EstimatedWips)
                .where(EstimatedWips.lazer_cutting_id == cut.id)
                .limit(1)
            )
        ).scalars().first()
        # ✅ 수정: EstimatedWips의 id를 output_placeholder으로 직접 사용
        # (SteelWip 상태와 무관하게 생성될 예상 재공품의 placeholder)
        output_placeholder_id = est.id if est else None

        actual_wip_id = cut.steel_wip_id
        source_wip = await db.get(SteelWip, cut.steel_wip_id) if cut.steel_wip_id else None
        is_allowed_raw_material = await _is_allowed_raw_material_spec(
            db,
            material=cut.input_material,
            thickness=(source_wip.thickness if source_wip else None),
            width=cut.input_width,
            length=cut.input_length,
        )
        if cut.steel_wip_id:
            # 원자재 규격 또는 야드 외부 WIP는 솔버에서 외부 입력처럼 취급한다.
            # 그렇지 않으면 wip_data에 없는 ID가 input_wip_id로 남아
            # rolling horizon lookahead에서 KeyError가 발생할 수 있다.
            if source_wip and (
                is_allowed_raw_material
                or source_wip.location_id is None
                or source_wip.stack_level is None
            ):
                actual_wip_id = None
        elif is_allowed_raw_material:
            actual_wip_id = None

        records.append({
            "id"                    : cut.id,
            "steel_wip_id"          : actual_wip_id,
            "source_steel_wip_id"   : cut.steel_wip_id,
            "estimated_cutting_time": cut.estimated_cutting_time or 1,
            "input_material"        : cut.input_material,
            "input_thickness"       : source_wip.thickness if source_wip else None,
            "input_width"           : cut.input_width,
            "input_length"          : cut.input_length,
            "has_output"            : est is not None,
            "output_placeholder_id" : output_placeholder_id,
        })
    return records


async def _query_wip_records(
    db:              AsyncSession,
    cutting_records: List[dict],
) -> List[dict]:
    """
    야드에 있는 모든 IN_STOCK WIP을 조회한다.
    cutting_records 에서 참조하는 WIP은 is_target=True 로 표시한다.
    """
    # 이번 작업지시서의 피킹 대상 WIP id 집합
    target_ids = {
        r["steel_wip_id"]
        for r in cutting_records
        if r.get("steel_wip_id")
    }

    # 야드 위치(loc_can_stock=1)에 있는 모든 IN_STOCK WIP
    rows = (
        await db.execute(
            select(SteelWip, Locations.loc_name)
            .join(Locations, SteelWip.location_id == Locations.id)
            .where(
                SteelWip.status == SteelWipStatus.IN_STOCK,
                Locations.loc_can_stock == 1,
            )
            .order_by(SteelWip.stack_level.asc())
        )
    ).all()

    records: List[dict] = []
    for wip, loc_name in rows:
        raw_level = wip.stack_level
        if raw_level is None or raw_level <= 0:
            # stack_level 미설정 WIP → level=1 (단독 적재로 가정, 솔버에 포함)
            logger.warning(
                "WIP id=%s (%s) stack_level=%s → level=1 로 가정 처리",
                wip.id, loc_name, raw_level,
            )
            raw_level = 1
        records.append({
            "id"         : wip.id,
            "loc_name"   : loc_name,
            "stack_level": raw_level,
            "is_target"  : wip.id in target_ids,
            "material"   : wip.material,
            "thickness"  : wip.thickness,
            "width"      : wip.width,
            "length"     : wip.length,
        })

    return records


async def _get_yard_location_map(db: AsyncSession) -> Dict[str, int]:
    """야드 스택 노드명 → locations.id 맵 (loc_can_stock=1)."""
    rows = (
        await db.execute(
            select(Locations.loc_name, Locations.id)
            .where(Locations.loc_can_stock == 1)
        )
    ).all()
    return {name: lid for name, lid in rows if name}


async def _get_buffer_location_map(db: AsyncSession) -> Dict[str, int]:
    """
    BUF-1 → locations.id 맵.
    없으면 자동 생성한다.
    """
    row = (
        await db.execute(
            select(Locations.loc_name, Locations.id)
            .where(Locations.loc_name == "BUF-1")
        )
    ).first()
    if row:
        return {"BUF-1": row.id}

    buf_id = await ensure_buffer_location(db)
    return {"BUF-1": buf_id}


async def _get_s4_location_map(db: AsyncSession) -> Dict[str, int]:
    """S4-1~S4-4 → locations.id 맵 (loc_can_stock=0)."""
    s4_names = ["S4-1", "S4-2", "S4-3", "S4-4"]
    rows = (
        await db.execute(
            select(Locations.loc_name, Locations.id)
            .where(Locations.loc_name.in_(s4_names))
        )
    ).all()
    return {name: lid for name, lid in rows if name}


# ═══════════════════════════════════════════════════════════════════════════════
# 내부 헬퍼 — 솔버 실행 (동기, executor 호출용)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_solver_sync(
    wip_records:     List[dict],
    cutting_records: List[dict],
) -> List[dict]:
    """
    동기 함수 — asyncio executor 에서 실행한다.
    CAASDy 솔버를 호출하고 action log를 반환한다.
    """
    from app.algorithms.caasdy.service_interface import (
        build_solver_input,
        run_caasdy_solver,
    )

    # ── 진단 로그: 수신 데이터 확인 ──────────────────────────────────────────
    logger.info(
        "[CAASDy 진단] wip_records=%d건, cutting_records=%d건",
        len(wip_records), len(cutting_records),
    )
    for wr in wip_records:
        logger.info(
            "  WIP id=%-4s loc=%-6s level=%-3s is_target=%s",
            wr.get("id"), wr.get("loc_name"), wr.get("stack_level"), wr.get("is_target"),
        )
    for cr in cutting_records:
        logger.info(
            "  JOB id=%-4s wip_id=%-4s cut_time=%s",
            cr.get("id"), cr.get("steel_wip_id"), cr.get("estimated_cutting_time"),
        )

    wip_data, job_data, inter_times, machine_times = build_solver_input(
        wip_records     = wip_records,
        cutting_records = cutting_records,
        data_dir        = _DATA_DIR,
    )

    # ── 진단 로그: 솔버 입력 상태 확인 ──────────────────────────────────────
    logger.info("[CAASDy 진단] build_solver_input 후:")
    from app.algorithms.caasdy.data.params import STACK_TO_NODE
    for wid, wd in wip_data.items():
        node = STACK_TO_NODE.get(wd.stack_id, f"?stack{wd.stack_id}")
        logger.info(
            "  WIPData wip_id=%-4s stack_id=%-3s node=%-6s level=%-3s is_output=%s",
            wd.wip_id, wd.stack_id, node, wd.level, wd.is_output_wip,
        )

    return run_caasdy_solver(
        wip_data      = wip_data,
        job_data      = job_data,
        inter_times   = inter_times,
        machine_times = machine_times,
    )


def _append_inbound_items(
    batch_plan: List[dict],
    cutting_records: List[dict],
    location_map: Dict[str, int],
    log: List[dict],
) -> None:
    """
    EstimatedWips 기반 INBOUND를 솔버 후처리로 계획 말미에 추가한다.

    입력재 피킹/장애물 처리와 출력재 적재를 분리해,
    출력재 때문에 중간 TEMP_MOVE가 다시 발생하는 비현실적 계획을 방지한다.
    """
    inbound_location_ids = [
        location_map[node]
        for node in _INBOUND_NODE_ORDER
        if node in location_map
    ]
    if not inbound_location_ids:
        return

    last_step = max((int(item.get("step", 0)) for item in batch_plan), default=0)
    last_clock = max((float(item.get("clock", 0.0)) for item in batch_plan), default=0.0)
    completion_clock_by_job: dict[int, float] = {}

    for entry in log:
        state_before = entry.get("state_before")
        state_after = entry.get("state_after")
        if state_before is None or state_after is None:
            continue
        completed_now = set(state_after.Q_done) - set(state_before.Q_done)
        if not completed_now:
            continue
        clock = float(getattr(state_after, "clock", 0.0) or 0.0)
        for job_id in completed_now:
            completion_clock_by_job[job_id] = clock

    inbound_idx = 0
    pending_inbounds: list[dict] = []

    for record in cutting_records:
        output_wip_id = record.get("output_placeholder_id")  # EstimatedWips.id
        if not record.get("has_output") or output_wip_id is None:
            continue

        to_location_id = inbound_location_ids[inbound_idx % len(inbound_location_ids)]
        inbound_idx += 1
        job_id = record.get("id")
        completion_clock = completion_clock_by_job.get(job_id)
        if completion_clock is None:
            last_clock += 1.0
            completion_clock = last_clock
        # ✅ INBOUND는 새로운 산출물을 만드는 작업
        # steel_wip_id = NULL (존재하지 않는 새 아이템)
        # estimated_wip_id = 발생 재공품 정보 참조
        pending_inbounds.append({
            "job_id": record.get("id"),
            "wip_id": None,  # ← steel_wip_id = NULL
            "estimated_wip_id": output_wip_id,  # ← EstimatedWips.id 저장 ✅
            "action": "INBOUND",
            "from_location_id": None,
            "to_location_id": to_location_id,
            "clock": completion_clock,
        })

    pending_inbounds.sort(key=lambda item: (float(item.get("clock", 0.0)), int(item.get("job_id", 0) or 0)))
    for item in pending_inbounds:
        last_step += 1
        item["step"] = last_step
        batch_plan.append(item)


# ═══════════════════════════════════════════════════════════════════════════════
# 내부 헬퍼 — DB 저장
# ═══════════════════════════════════════════════════════════════════════════════

async def _save_batch_plan(
    db:          AsyncSession,
    scenario_id: int,
    batch_plan:  List[dict],
) -> None:
    """
    batch_plan dict 목록을 단일 Batch(batch_order=1)로 저장한다.

    각 item 키:
      job_id, wip_id, action, from_location_id, to_location_id, step, clock
    """
    # 단일 Batch 생성
    batch = Batch(scenario_id=scenario_id, batch_order=1)
    db.add(batch)
    await db.flush()   # batch.id 확보

    cutting_stmt = select(LazerCutting).where(LazerCutting.scenario_id == scenario_id)
    cuttings = (await db.execute(cutting_stmt)).scalars().all()
    for cutting in cuttings:
        cutting.batch_id = batch.id

    for order, item in enumerate(batch_plan, start=1):
        action_str = item["action"]

        # enum 변환 — 없는 값이면 RELOCATE 폴백 (안전 처리)
        if action_str == "DIRECT_START":
            action_enum = BatchItemsBatchItemAction.RELOCATE
        else:
            try:
                action_enum = BatchItemsBatchItemAction(action_str)
            except ValueError:
                logger.warning("알 수 없는 action '%s', RELOCATE로 폴백", action_str)
                action_enum = BatchItemsBatchItemAction.RELOCATE

        db.add(BatchItems(
            batch_id             = batch.id,
            steel_wip_id         = item.get("wip_id"),
            # ✅ INBOUND의 경우 EstimatedWips 정보 저장
            estimated_wip_id     = item.get("estimated_wip_id"),
            batch_item_action    = action_enum,
            status               = BatchItemsStatus.BEFORE_PENDING,
            batch_item_order     = order,
            from_location        = item.get("from_location_id"),
            to_location          = item.get("to_location_id"),
            expected_start_time  = int(item.get("clock", 0)),
            expected_running_time= 0,
        ))

    await db.flush()
