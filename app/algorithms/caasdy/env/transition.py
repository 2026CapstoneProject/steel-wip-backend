"""
전이 함수: S_{t+1} = S^M(S_t, x_t, W_{t+1})

Phase12 1차 구현 추가:
  - K_mach를 기반으로 mach_slots(2x2)를 동기화한다.
  - 아직 슬롯 제약을 강제하지는 않고, 서비스/UI용 상태를 먼저 제공한다.
"""

from typing import Dict, Optional
import numpy as np
from ..data.params import (
    DELTA_MIN, STACK_TO_NODE, MACHINE_NODE, SIGMA_PTIME, BUFFER_NODE,
)
from ..data.loader import WIPData, JobData, get_crane_time
from .state import State, MachinePhase
from .slot_layout import (
    build_raw_job_layout,
    empty_slots,
    is_layout_consistent,
    pack_wips_into_slots,
    place_wip_in_slots,
    SLOT_ORDER,
)
from .actions import (
    Action, CraneAction, ProdAction,
    CRANE_PICKING, CRANE_STORE, CRANE_MOVE, CRANE_TEMP_MOVE,
    CRANE_RESTORE, CRANE_PRE_POSITION, CRANE_WAIT,
    PROD_START, PROD_DIRECT_START, PROD_CONTINUE, PROD_NONE,
)

# 확률적 모드 플래그 (main.py의 --stochastic 옵션으로 활성화 가능)
_stochastic_mode: bool = False


def set_stochastic(enabled: bool) -> None:
    """확률적 생산시간 모드 전환 (main.py에서 호출)"""
    global _stochastic_mode
    _stochastic_mode = enabled


# ── Phase 10: 이종 WIP 동시 투입 (co-loading) 모드 플래그 ─────────────────────
_co_loading_mode: bool = False


def set_co_loading(enabled: bool) -> None:
    """이종 WIP 동시 투입(co-loading) 모드 전환 (Phase10에서 호출)"""
    global _co_loading_mode
    _co_loading_mode = enabled


# ── Phase 11: 버퍼 = 가장 가까운 적재 공간 소요시간 모드 플래그 ─────────────────
# 활성화 시 CRANE_TEMP_MOVE / CRANE_RESTORE / CRANE_PRE_POSITION 의 τ를
# 고정 BUFFER_NODE(B-4) 거리 대신 실제 최근접 스택까지의 최소 거리로 계산한다.
# 기본값 False → Phase 1~10은 기존 B-4 기준 유지.
_buffer_nearest_mode: bool = False


def set_buffer_nearest_mode(enabled: bool) -> None:
    """버퍼 이동 시간을 최근접 스택 거리로 계산하는 모드 전환 (Phase11에서 호출)"""
    global _buffer_nearest_mode
    _buffer_nearest_mode = enabled


def _nearest_stack_time_from(
    src_node: str,
    inter_times: Dict,
    machine_times: Dict,
) -> float:
    """
    src_node에서 다른 모든 스택까지의 최소 이동 시간 (분).

    버퍼가 물리적으로 '가장 가까운 빈 적재 공간'에 해당한다고 볼 때,
    CRANE_TEMP_MOVE 의 τ로 사용한다.
    """
    best = float("inf")
    for node in STACK_TO_NODE.values():
        if node == src_node:
            continue
        t = get_crane_time(src_node, node, inter_times, machine_times)
        if t < best:
            best = t
    return best if best < float("inf") else DELTA_MIN


def _nearest_stack_node_from(
    src_node: str,
    inter_times: Dict,
    machine_times: Dict,
) -> str:
    """
    src_node에서 가장 가까운 '다른' 스택 노드를 반환한다.

    Phase11의 nearest-buffer 모드에서 TEMP_MOVE 후 크레인 위치를
    더 일관되게 갱신하기 위해 사용한다.
    """
    best_node = src_node
    best = float("inf")
    for node in STACK_TO_NODE.values():
        if node == src_node:
            continue
        t = get_crane_time(src_node, node, inter_times, machine_times)
        if t < best:
            best = t
            best_node = node
    return best_node


def _nearest_stack_time_to(
    dst_node: str,
    inter_times: Dict,
    machine_times: Dict,
) -> float:
    """
    다른 모든 스택에서 dst_node까지의 최소 이동 시간 (분).

    버퍼가 '가장 가까운 위치'에 있다고 볼 때,
    CRANE_RESTORE / CRANE_PRE_POSITION 의 τ로 사용한다.
    """
    best = float("inf")
    for node in STACK_TO_NODE.values():
        if node == dst_node:
            continue
        t = get_crane_time(node, dst_node, inter_times, machine_times)
        if t < best:
            best = t
    return best if best < float("inf") else DELTA_MIN


def _nearest_stack_time_to_machine(
    machine_times: Dict[str, float],
) -> float:
    """버퍼에서 설비로 복귀하는 시간을 최근접 스택 기준으로 계산한다."""
    best = float("inf")
    for node in STACK_TO_NODE.values():
        t = machine_times.get(node, float("inf"))
        if t < best:
            best = t
    return best if best < float("inf") else DELTA_MIN


# 크레인 행동별 소요시간 τ(x_t^crane)

def get_tau(
    crane: CraneAction,
    state: State,
    inter_times: Dict,
    machine_times: Dict,
) -> float:
    """
    크레인 행동 소요시간 τ(x_t^crane) 계산 (분)
    c_{t+1} = c_t + τ(x_t^crane)
    τ(WAIT) = DELTA_MIN > 0 (무한루프 방지)
    """
    ctype = crane.type

    if ctype == CRANE_WAIT:
        # DIRECT_START도 CRANE_WAIT을 사용하므로 별도 처리 불필요
        return DELTA_MIN

    if ctype == CRANE_PICKING:
        # 야드 PICKING: 현재 위치 → 스택 → 설비
        # 버퍼 PICKING(src_stack=None): 버퍼를 최근접 임시공간으로 보고 설비까지의
        # 최소 복귀 시간만 사용한다.
        if crane.src_stack is None:
            return _nearest_stack_time_to_machine(machine_times)
        src_node = STACK_TO_NODE.get(crane.src_stack, state.crane_loc)
        # 스택까지 이동 + 설비까지 이동 (단순화: 설비 이동시간만 사용)
        t_to_stack = get_crane_time(state.crane_loc, src_node,
                                    inter_times, machine_times)
        t_to_mach  = machine_times.get(src_node, 5.0)
        return t_to_stack + t_to_mach

    if ctype == CRANE_STORE:
        # 설비 → 스택 이동
        dst_node = STACK_TO_NODE.get(crane.dst_stack, state.crane_loc)
        t_to_stack = machine_times.get(dst_node, 5.0)
        return t_to_stack

    if ctype == CRANE_MOVE:
        src_node = STACK_TO_NODE.get(crane.src_stack, state.crane_loc)
        dst_node = STACK_TO_NODE.get(crane.dst_stack, state.crane_loc)
        return get_crane_time(src_node, dst_node, inter_times, machine_times)

    if ctype == CRANE_TEMP_MOVE:
        src_node = STACK_TO_NODE.get(crane.src_stack, state.crane_loc)
        if _buffer_nearest_mode:
            # Phase 11: 버퍼 = 가장 가까운 적재 공간 → 최소 이동 시간 사용
            return _nearest_stack_time_from(src_node, inter_times, machine_times)
        # Phase 1~10: B-4 고정 버퍼 위치까지의 이동 시간
        return get_crane_time(src_node, BUFFER_NODE, inter_times, machine_times)

    if ctype == CRANE_RESTORE:
        dst_node = STACK_TO_NODE.get(crane.dst_stack, state.crane_loc)
        if _buffer_nearest_mode:
            # Phase 11: 버퍼 ≈ 가장 가까운 위치 → 목적지까지 최소 이동 시간
            return _nearest_stack_time_to(dst_node, inter_times, machine_times)
        return get_crane_time(BUFFER_NODE, dst_node, inter_times, machine_times)

    if ctype == CRANE_PRE_POSITION:
        dst_node = STACK_TO_NODE.get(crane.dst_stack, state.crane_loc)
        if _buffer_nearest_mode:
            # Phase 11: RESTORE와 동일한 nearest-mode 적용
            return _nearest_stack_time_to(dst_node, inter_times, machine_times)
        return get_crane_time(BUFFER_NODE, dst_node, inter_times, machine_times)

    return DELTA_MIN


# 크레인 위치 갱신

def _new_crane_loc(
    crane: CraneAction,
    old_loc: str,
    inter_times: Optional[Dict] = None,
    machine_times: Optional[Dict] = None,
) -> str:
    """행동 후 크레인이 있을 노드 이름"""
    ctype = crane.type
    if ctype == CRANE_PICKING:
        return MACHINE_NODE
    if ctype == CRANE_STORE:
        dst = STACK_TO_NODE.get(crane.dst_stack)
        return dst if dst else old_loc
    if ctype in (CRANE_MOVE,):
        dst = STACK_TO_NODE.get(crane.dst_stack)
        return dst if dst else old_loc
    if ctype == CRANE_TEMP_MOVE:
        if _buffer_nearest_mode and inter_times is not None and machine_times is not None:
            src = STACK_TO_NODE.get(crane.src_stack, old_loc)
            return _nearest_stack_node_from(src, inter_times, machine_times)
        return BUFFER_NODE   # B-4: 물리 버퍼 위치
    if ctype in (CRANE_RESTORE, CRANE_PRE_POSITION):
        dst = STACK_TO_NODE.get(crane.dst_stack)
        return dst if dst else old_loc
    return old_loc   # WAIT


def _sync_mach_slots(
    s: State,
    wip_data: Dict[int, WIPData],
) -> None:
    """
    Phase12 1차 구현용 슬롯 동기화.

    슬롯 값 규약 (state.py _default_mach_slots 참고):
      None       — 빈 슬롯
      wip_id > 0 — 해당 WIP 점유
      0          — 원자재 런(DIRECT_START) 점유

    상태별 동기화 정책
    ──────────────────
    EMPTY / LOADING
    BUSY (K_mach ≠ ∅)   K_mach 기준, short_side 내림차순 first-fit 배치.
    ─────────────────────────────────────────────────────────────────────
    BUSY (K_mach = ∅)   DIRECT_START(원자재) 런 중.
                        물리 WIP 추적이 없으므로 전 슬롯을 0으로 채워
                        UI에서 "원자재 가공 중"으로 표시할 수 있게 한다.
    ─────────────────────────────────────────────────────────────────────
    BLOCKED             출력재가 물리적으로 설비 위에 있으므로
                        O_wait 기준으로 슬롯을 채운다.
                        K_mach는 이미 frozenset()이므로 사용하지 않는다.

    TODO(Phase12 2차):
      - 세로 긴 자재/원자재의 2칸 점유 강제
      - dead-space 최소화 배치
      - slot-aware action / transition
    """
    if not hasattr(s, "mach_slots"):
        return

    if s.phase == MachinePhase.EMPTY:
        # 설비 비어있음 → 슬롯 전체 초기화
        s.mach_slots = empty_slots()
        s.mach_footprints = {}

    elif s.phase == MachinePhase.BLOCKED:
        # 출력재(O_wait)가 물리적으로 설비 위에 있음 → O_wait 기준으로 pack
        ok, slots, footprints = pack_wips_into_slots(s.O_wait, wip_data)
        if not ok:
            slots = empty_slots()
            footprints = {}
        s.mach_slots = slots
        s.mach_footprints = footprints

    elif s.phase == MachinePhase.BUSY and len(s.K_mach) == 0:
        # DIRECT_START(원자재 런): _update_machine에서 raw piece footprint를
        # 이미 기록한 경우 그대로 유지한다.
        # 만약 비어 있다면 fallback으로 "가공 중" 표시만 둔다.
        if not s.mach_footprints:
            s.mach_slots = {sl: 0 for sl in SLOT_ORDER}
            s.mach_footprints = {}

    else:
        # LOADING 또는 BUSY(K_mach ≠ ∅):
        # _update_machine에서 slot-aware PICKING이 이미 만든 layout이
        # 현재 K_mach와 일관되면 그대로 유지하고, 아니면 fallback pack을 사용한다.
        if is_layout_consistent(s.mach_slots, s.mach_footprints, s.K_mach, wip_data):
            return

        ok, slots, footprints = pack_wips_into_slots(s.K_mach, wip_data)
        if not ok:
            slots = empty_slots()
            footprints = {}
        s.mach_slots = slots
        s.mach_footprints = footprints


# 메인 전이 함수

def transition(
    state: State,
    action: Action,
    wip_data: Dict[int, WIPData],
    job_data:  Dict[int, JobData],
    inter_times: Dict,
    machine_times: Dict,
) -> State:
    """
    S_{t+1} = S^M(S_t, x_t, W_{t+1})
    결정론 버전: W_{t+1}의 불확실 요소는 무시 (ω^ptime=0, ω^order=∅)
    """
    s = state.copy()
    crane = action.crane
    prod  = action.prod
    tau   = get_tau(crane, state, inter_times, machine_times)

    _update_machine(s, crane, prod, tau, wip_data, job_data)
    _sync_mach_slots(s, wip_data)

    _update_yard(s, crane)

    s.crane_loc = _new_crane_loc(crane, state.crane_loc, inter_times, machine_times)

    s.clock += tau
    # rem_shift, is_unm은 clock으로부터 즉시 계산 가능하므로 별도 저장 안 함

    s.step += 1
    return s


# 내부: 설비 상태 전이

def _update_machine(
    s: State,
    crane: CraneAction,
    prod:  ProdAction,
    tau:   float,
    wip_data: Dict[int, WIPData],
    job_data:  Dict[int, JobData],
) -> None:
    """
    K_mach, j_mach, u_short, u_long, eta, phase, O_wait, Q_rem, Q_done 갱신
    """
    m = s   # 직접 수정

    if m.phase == MachinePhase.BUSY:
        new_eta = max(0.0, m.eta - tau)
        m.eta = new_eta
        if m.eta == 0.0:
            # 생산 완료 — j_mach + j_mach_set 모두 처리
            completed_jobs: set = set()
            if m.j_mach is not None:
                completed_jobs.add(m.j_mach)
            completed_jobs |= set(m.j_mach_set)   # Phase 10 co-loading

            m.K_mach  = frozenset()
            m.u_short = 0.0
            m.u_long  = 0.0

            # Q_rem에서 제거, Q_done에 추가 + output WIP 수집
            output_wids: set = set()
            for q in completed_jobs:
                if q in m.Q_rem:
                    m.Q_rem  = m.Q_rem  - {q}
                    m.Q_done = m.Q_done | {q}
                job = job_data.get(q)
                if job is not None and job.generates_output and job.output_wip_id is not None:
                    output_wids.add(job.output_wip_id)

            # j_mach_set 초기화
            m.j_mach_set = frozenset()

            if output_wids:
                m.phase  = MachinePhase.BLOCKED
                m.O_wait = m.O_wait | frozenset(output_wids)
            else:
                m.phase  = MachinePhase.EMPTY
                m.j_mach = None
        return   # BUSY 상태에서는 아래 로직 실행 안 함

    if crane.type == CRANE_PICKING:
        k    = crane.wip_id
        q    = crane.job_id
        wip  = wip_data[k]

        if m.phase == MachinePhase.EMPTY:
            # case 1: 빈 설비에 첫 PICKING → LOADING으로 전환
            m.K_mach  = frozenset([k])
            m.j_mach  = q
            m.u_short = wip.short_side
            m.u_long  = wip.long_side
            m.phase   = MachinePhase.LOADING

        elif m.phase == MachinePhase.LOADING:
            # case 2: LOADING 중 추가 PICKING
            m.K_mach  = m.K_mach | {k}
            m.u_short = m.u_short + wip.short_side
            m.u_long  = max(m.u_long, wip.long_side)
            # Phase 10 co-loading: j_mach 외 다른 job이면 j_mach_set에 추가
            if _co_loading_mode and q != m.j_mach:
                m.j_mach_set = m.j_mach_set | {q}
            # j_mach 유지 (primary job)

        # Phase12: 지정된 시작 슬롯에 실제 footprint를 기록한다.
        # 일반 WIP는 1칸, 세로 긴 WIP는 2칸 점유.
        if hasattr(m, "mach_slots") and crane.slot is not None:
            placed = place_wip_in_slots(m.mach_slots, k, wip, crane.slot)
            if placed is not None:
                new_slots, footprint = placed
                m.mach_slots = new_slots
                m.mach_footprints = dict(m.mach_footprints)
                m.mach_footprints[k] = footprint

        return

    if prod.type == PROD_START and m.phase == MachinePhase.LOADING:
        q = prod.job_id
        job = job_data[q]
        m.phase = MachinePhase.BUSY
        # Phase 10 co-loading: 설비에 올라간 모든 job의 가공시간 합산
        # (순차 절단 → 전체 사이클 시간 = sum)
        if _co_loading_mode and m.j_mach_set:
            all_active = {q} | set(m.j_mach_set)
            ptime = sum(
                job_data[j].process_time for j in all_active if j in job_data
            )
        else:
            ptime = job.process_time   # p_{q_t^mach}
        # 확률적 생산시간: ω_{t+1}^ptime ~ N(0, σ) (SDAM )
        if _stochastic_mode and SIGMA_PTIME > 0.0:
            noise = np.random.normal(0.0, SIGMA_PTIME)
            ptime = max(DELTA_MIN, ptime + noise)
        m.eta = ptime
        return

    # 크레인 PICKING 없이 바로 가공 시작. K_mach는 비워두고 cap 값으로 설정.
    if prod.type == PROD_DIRECT_START and m.phase == MachinePhase.EMPTY:
        q   = prod.job_id
        job = job_data[q]
        m.phase   = MachinePhase.BUSY
        m.j_mach  = q
        m.K_mach  = frozenset()        # 물리적 WIP 추적 없음 (원자재)
        m.u_short = job.cap_short      # 배치 용량 전체 사용으로 간주
        m.u_long  = job.cap_long
        ptime = job.process_time
        if _stochastic_mode and SIGMA_PTIME > 0.0:
            noise = np.random.normal(0.0, SIGMA_PTIME)
            ptime = max(DELTA_MIN, ptime + noise)
        m.eta = ptime
        # Phase12: raw piece의 slot layout을 기록
        if hasattr(m, "mach_slots"):
            ok, slots, footprints = build_raw_job_layout(job)
            if ok:
                m.mach_slots = slots
                m.mach_footprints = footprints
            else:
                m.mach_slots = {sl: 0 for sl in SLOT_ORDER}
                m.mach_footprints = {}
        return

    if crane.type == CRANE_STORE and m.phase == MachinePhase.BLOCKED:
        k = crane.wip_id
        m.O_wait = m.O_wait - {k}
        if len(m.O_wait) == 0:
            # 모든 출력재 적재 완료 → EMPTY로 전환
            m.phase  = MachinePhase.EMPTY
            m.j_mach = None
        return   # BLOCKED 상태의 STORE는 여기서 끝


# 내부: 야드 상태 전이

def _update_yard(s: State, crane: CraneAction) -> None:
    """
    stacks 딕셔너리 갱신
    PICKING: 스택에서 WIP 제거
    STORE: 스택에 WIP 추가 (출력재)
    MOVE, TEMP_MOVE, RESTORE
    """
    if crane.type == CRANE_PICKING:
        sid = crane.src_stack
        if sid is None:
            # Phase 2: 버퍼 WIP 직접 PICKING
            if crane.wip_id in s.buffer_wips:
                s.buffer_wips = s.buffer_wips - {crane.wip_id}
                s.buffer_cap += 1
        else:
            stk = s.stacks.get(sid, [])
            if stk and stk[-1] == crane.wip_id:
                s.stacks[sid] = stk[:-1]   # 최상단 제거

    elif crane.type == CRANE_STORE:
        # Phase 4: output_wip_id > 0인 실제 출력재는 해당 스택에 적재.
        # wid <= 0은 구버전 토큰 방식 (Phase 4에서는 미사용).
        wid = crane.wip_id
        if wid is not None and wid > 0:
            sid = crane.dst_stack
            stk = s.stacks.get(sid, [])
            s.stacks[sid] = stk + [wid]

    elif crane.type == CRANE_MOVE:
        src_stk = s.stacks.get(crane.src_stack, [])
        if src_stk and src_stk[-1] == crane.wip_id:
            s.stacks[crane.src_stack] = src_stk[:-1]
            dst_stk = s.stacks.get(crane.dst_stack, [])
            s.stacks[crane.dst_stack] = dst_stk + [crane.wip_id]

    elif crane.type == CRANE_TEMP_MOVE:
        sid = crane.src_stack
        stk = s.stacks.get(sid, [])
        if stk and stk[-1] == crane.wip_id:
            s.stacks[sid] = stk[:-1]
            s.buffer_wips = s.buffer_wips | {crane.wip_id}
            s.buffer_cap  = s.buffer_cap - 1

    elif crane.type == CRANE_RESTORE:
        sid = crane.dst_stack
        stk = s.stacks.get(sid, [])
        s.stacks[sid]  = stk + [crane.wip_id]
        s.buffer_wips  = s.buffer_wips - {crane.wip_id}
        s.buffer_cap   = s.buffer_cap + 1

    elif crane.type == CRANE_PRE_POSITION:
        # Phase 3: RESTORE와 동일한 물리적 효과 (버퍼 → 야드 스택 top)
        # 전략적 차이는 feasibility/greedy에서 선택 로직으로 처리
        sid = crane.dst_stack
        stk = s.stacks.get(sid, [])
        s.stacks[sid]  = stk + [crane.wip_id]
        s.buffer_wips  = s.buffer_wips - {crane.wip_id}
        s.buffer_cap   = s.buffer_cap + 1
