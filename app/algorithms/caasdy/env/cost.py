"""
C(S_t, x_t, W_{t+1}) =
    c^rel  · Δ_t^rel
  + c^temp · Δ_t^temp
  - r^fill · Δ_t^fill
  - r^unm  · Δ_t^unm

Terminal penalty:
  P_RUN     · |Q_rem|
+ P_BUFFER  · |buffer_wips|
+ P_MACH    · (|K_mach| + |O_wait|)
+ P_BLOCKER · Σ_{job ∈ Q_rem} blocker_count(input_wip)
"""

from typing import Dict, Optional

from ..data.params import (
    C_REL, C_TEMP, R_FILL, R_UNM,
    W_SHORT, W_LONG,
    P_RUN, P_BUFFER, P_MACH, P_BLOCKER,
)
from ..data.loader import JobData
from .state import State, MachinePhase
from .actions import Action, CRANE_MOVE, CRANE_TEMP_MOVE, PROD_START, PROD_DIRECT_START


def step_cost(
    state:    State,
    action:   Action,
    job_data: Dict[int, JobData],
    tau:      float,
) -> float:
    """
    단계별 비용 C(S_t, x_t, W_{t+1})
    양수 = 비용, 음수 = 보상
    최소화 목적이므로 보상 항목은 음수로 반환
    """
    crane = action.crane
    prod  = action.prod
    cost  = 0.0

    if crane.type == CRANE_MOVE:
        cost += C_REL

    if crane.type == CRANE_TEMP_MOVE:
        cost += C_TEMP

    if prod.type == PROD_START and state.j_mach is not None:
        q = state.j_mach
        job = job_data.get(q)
        if job is not None and job.cap_short > 0 and job.cap_long > 0:
            fill_util = (
                W_SHORT * state.u_short / job.cap_short
                + W_LONG  * state.u_long  / job.cap_long
            )
            cost -= R_FILL * fill_util   # 보상 → 음수

    if prod.type == PROD_DIRECT_START:
        job = job_data.get(prod.job_id)
        if job is not None:
            # 원자재는 배치 정원을 채운 것으로 처리 → fill_util = 1.0
            cost -= R_FILL * 1.0   # 보상 → 음수

    if state.phase == MachinePhase.BUSY:
        # 무인가공 구간 겹침 계산 (4단계 사이클: 점심 + 야간 양쪽 합산)
        cfg   = state.shift_cfg
        t_rel = state.clock % cfg.cycle_minutes
        overlap = cfg.unm_overlap(t_rel, tau)
        cost -= R_UNM * overlap   # 보상 → 음수

    return cost


def terminal_cost(
    state: State,
    job_data: Optional[Dict[int, JobData]] = None,
) -> float:
    """
    에피소드 종료 시 terminal penalty C_T(S_T)
    - 미완료 job 수
    - 버퍼 미복원 WIP 수
    - 설비 위 미시작 WIP 수
    - 생산 완료 후 미적재 출력재 수
    - blocker WIP 수 (필요 WIP 위에 눌린 WIP, job_data 제공 시)
    """
    penalty = 0.0
    penalty += P_RUN    * len(state.Q_rem)
    penalty += P_BUFFER * len(state.buffer_wips)
    penalty += P_MACH   * len(state.K_mach)
    penalty += P_MACH   * len(state.O_wait)

    # P_BLOCKER: 미완료 run의 input_wip 위에 쌓인 blocker WIP 수
    if job_data is not None:
        for jid in state.Q_rem:
            if jid not in job_data:
                continue
            target_wip = job_data[jid].input_wip_id
            if target_wip <= 0:
                continue
            for stack in state.stacks.values():
                if target_wip not in stack:
                    continue
                pos = stack.index(target_wip)
                # target_wip 위에 있는 WIP 수 = blocker count
                blocker_count = len(stack) - pos - 1
                penalty += P_BLOCKER * blocker_count
                break

    return penalty


def episode_summary(log: list, job_data: Optional[Dict[int, JobData]] = None) -> dict:
    """시뮬레이션 로그로부터 에피소드 요약 통계 계산"""
    total_cost = sum(entry["cost"] for entry in log)
    total_cost += terminal_cost(log[-1]["state_after"], job_data=job_data) if log else 0.0

    n_pickings    = sum(1 for e in log if e["action"].crane.type == "PICKING")
    n_starts      = sum(1 for e in log if e["action"].prod.type  == "START_PROCESS")
    n_stores      = sum(1 for e in log if e["action"].crane.type == "STORE")
    n_moves       = sum(1 for e in log if e["action"].crane.type == "MOVE")
    n_temp_moves  = sum(1 for e in log if e["action"].crane.type == "TEMP_MOVE")
    n_restores    = sum(1 for e in log if e["action"].crane.type == "RESTORE")
    n_pre_pos     = sum(1 for e in log if e["action"].crane.type == "PRE_POSITION")
    # START_PROCESS는 crane=WAIT를 사용하지만, 운영상 "대기"라기보다
    # 생산 시작 이벤트이므로 WAIT 집계에서는 제외한다.
    n_waits  = sum(
        1
        for e in log
        if e["action"].crane.type == "WAIT"
        and e["action"].prod.type != PROD_START
    )

    # 크레인 물리 이동 합계 (WAIT 제외한 모든 crane action)
    n_crane_moves_total = n_pickings + n_stores + n_moves + n_temp_moves + n_restores + n_pre_pos
    n_relocations_total = n_moves + n_temp_moves + n_restores + n_pre_pos

    # ── 시간 기반 KPI ────────────────────────────────────────────────────────
    # 재배치 시간: MOVE / TEMP_MOVE / RESTORE / PRE_POSITION에 해당하는 τ 합
    relocation_action_types = {"MOVE", "TEMP_MOVE", "RESTORE", "PRE_POSITION"}
    total_relocation_minutes = sum(
        float(e.get("tau", 0.0))
        for e in log
        if e["action"].crane.type in relocation_action_types
    )

    # 크레인 총 작업시간: WAIT를 제외한 모든 crane action의 τ 합
    total_crane_busy_minutes = sum(
        float(e.get("tau", 0.0))
        for e in log
        if e["action"].crane.type != "WAIT"
    )

    final_state = log[-1]["state_after"] if log else None

    # ── 자재 사용 수: 완료된 Job 기준 ────────────────────────────────────────
    # 재공품(WIP)  : has_external_input=False → 야드 재고를 크레인으로 투입
    # 원자재(Raw)  : has_external_input=True  → 외부 투입 (크레인 PICKING 불필요)
    n_wip_used = 0
    n_raw_used = 0
    if final_state and job_data:
        for jid in final_state.Q_done:
            jd = job_data.get(jid)
            if jd is None:
                continue
            if jd.has_external_input:
                n_raw_used += 1
            else:
                n_wip_used += 1

    # ── 하루 생산량 환산 ──────────────────────────────────────────────────────
    # 한 사이클(cycle_minutes) = 1 영업일 기준으로 완료 Job 수를 환산
    clock_end  = final_state.clock if final_state else 0.0
    cycle_min  = final_state.shift_cfg.cycle_minutes if final_state else 1260.0
    jobs_done  = len(final_state.Q_done) if final_state else 0
    days_elapsed = clock_end / cycle_min if cycle_min > 0 else 1.0
    throughput_per_day = round(jobs_done / days_elapsed, 4) if days_elapsed > 0 else 0.0

    # ── 총 소요 시간 (작업 wall-clock 분) ────────────────────────────────────
    total_work_minutes = clock_end

    # ── 총 설비 가동 시간 및 가동률 ───────────────────────────────────────────
    total_processing_minutes = 0.0
    if job_data:
        for entry in log:
            prod = entry["action"].prod
            if prod.type in (PROD_START, PROD_DIRECT_START):
                jd = job_data.get(prod.job_id)
                if jd is not None:
                    total_processing_minutes += jd.process_time

    utilization_episode = (
        total_processing_minutes / total_work_minutes
        if total_work_minutes > 0
        else 0.0
    )
    # 하루 환산 가동률은 현재 교대 사이클 전체를 하루 단위로 보는 정의이므로,
    # episode 기준 비율과 동일한 값을 별도 KPI로 저장한다.
    utilization_per_day = utilization_episode

    crane_utilization_episode = (
        total_crane_busy_minutes / total_work_minutes
        if total_work_minutes > 0
        else 0.0
    )
    relocation_time_share = (
        total_relocation_minutes / total_work_minutes
        if total_work_minutes > 0
        else 0.0
    )
    avg_relocation_minutes = (
        total_relocation_minutes / n_relocations_total
        if n_relocations_total > 0
        else 0.0
    )

    return {
        "total_cost":           total_cost,
        "n_steps":              len(log),
        # 기존 KPI
        "n_pickings":           n_pickings,
        "n_starts":             n_starts,
        "n_stores":             n_stores,
        "n_moves":              n_moves,
        "n_temp_moves":         n_temp_moves,
        "n_restores":           n_restores,
        "n_pre_positions":      n_pre_pos,
        "n_waits":              n_waits,
        "jobs_done":            jobs_done,
        "jobs_remain":          len(final_state.Q_rem) if final_state else 0,
        "clock_end":            clock_end,
        # ── 신규 KPI ──────────────────────────────────────────────────────────
        "n_wip_used":           n_wip_used,          # 재공품 소비 수
        "n_raw_used":           n_raw_used,          # 원자재 소비 수
        "n_crane_moves_total":  n_crane_moves_total, # 크레인 물리 이동 합계
        "n_relocations_total":  n_relocations_total, # 재배치 총합
        "throughput_per_day":   throughput_per_day,  # 일 생산량 환산
        "total_work_minutes":   total_work_minutes,  # 총 소요 시간 (분)
        "total_processing_minutes": total_processing_minutes, # 설비 총 가동 시간 (분)
        "utilization_episode":  utilization_episode, # 에피소드 기준 설비 가동률
        "utilization_per_day":  utilization_per_day, # 하루 환산 설비 가동률
        "total_relocation_minutes": total_relocation_minutes,   # 재배치 총 소요 시간 (분)
        "total_crane_busy_minutes": total_crane_busy_minutes,   # 크레인 총 작업 시간 (분)
        "crane_utilization_episode": crane_utilization_episode, # 크레인 가동률
        "relocation_time_share": relocation_time_share,         # 총 작업시간 중 재배치 시간 비중
        "avg_relocation_minutes": avg_relocation_minutes,       # 재배치 1회당 평균 시간
    }
