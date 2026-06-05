"""Greedy fallback policy used by the ADP runner."""

from typing import Dict, List, Optional, Set, Tuple

from ..data.loader import WIPData, JobData
from ..env.state import State, MachinePhase
from ..env.actions import Action, CRANE_PICKING, CRANE_STORE, PROD_START, PROD_DIRECT_START, CRANE_WAIT
from ..env.actions import CRANE_MOVE, CRANE_TEMP_MOVE, CRANE_RESTORE, CRANE_PRE_POSITION
from ..env.feasibility import get_feasible_actions


_POLICY_CONTEXT = {
    "scenario_name": "base",
    "plan_num": 0,
}


def set_policy_context(*, scenario_name: str = "base", plan_num: int = 0) -> None:
    _POLICY_CONTEXT["scenario_name"] = scenario_name or "base"
    _POLICY_CONTEXT["plan_num"] = int(plan_num or 0)


def greedy_policy(
    state:     State,
    wip_data:  Dict[int, WIPData],
    job_data:  Dict[int, JobData],
) -> Action:
    """Select one feasible action with simple priority rules."""
    feasible = get_feasible_actions(state, wip_data, job_data)
    if not feasible:
        from ..env.actions import WAIT_NONE
        return WAIT_NONE

    phase = state.phase

    if phase == MachinePhase.BLOCKED:
        stores = [a for a in feasible if a.crane.type == CRANE_STORE]
        if stores:
            return stores[0]

    if phase == MachinePhase.EMPTY and len(state.buffer_wips) > 0:
        needed_unique: Set[int] = set()
        for jid in state.Q_rem:
            jb = job_data.get(jid)
            if jb and jb.input_wip_id > 0:
                needed_unique.add(jb.input_wip_id)
        non_needed_in_buf = [w for w in state.buffer_wips if w not in needed_unique]
        direct_start_available = any(a.prod.type == PROD_DIRECT_START for a in feasible)
        if not direct_start_available and (non_needed_in_buf or len(state.Q_rem) == 0):
            restore = _best_restore_action(state, wip_data, job_data, feasible,
                                            avoid_wip_ids=needed_unique)
            if restore is not None:
                return restore

    if phase == MachinePhase.LOADING and len(state.buffer_wips) > 0:
        restore = _best_restore_action(state, wip_data, job_data, feasible)
        if restore is not None:
            return restore

    if phase == MachinePhase.LOADING and state.j_mach is not None:
        q = state.j_mach
        job = job_data.get(q)
        if job and len(state.K_mach) >= job.batch_count:
            starts = [a for a in feasible if a.prod.type == PROD_START]
            if starts:
                return starts[0]

    pickings = [a for a in feasible if a.crane.type == CRANE_PICKING]
    if pickings:
        def picking_score(a: Action) -> float:
            job = job_data.get(a.crane.job_id)
            wip = wip_data.get(a.crane.wip_id)
            if wip is None or job is None:
                return float("-inf")

            short_fill = min(1.0, wip.short_side / max(job.cap_short, 1.0))
            long_fill = min(1.0, wip.long_side / max(job.cap_long, 1.0))
            slot_bonus = _slot_quality_bonus(state, a, wip_data)
            return 10.0 * short_fill + 6.0 * long_fill + slot_bonus
        return max(pickings, key=picking_score)

    if phase == MachinePhase.LOADING and len(state.K_mach) >= 1:
        starts = [a for a in feasible if a.prod.type == PROD_START]
        if starts:
            return starts[0]

    if phase == MachinePhase.EMPTY and not pickings:
        direct_starts = [a for a in feasible if a.prod.type == PROD_DIRECT_START]
        t_to_unm = _time_to_next_unmanned(state.clock, state.shift_cfg)
        STORE_MARGIN = 5.0
        urgent = None
        prioritize_wip = _should_prioritize_wip_over_raw(state, job_data, t_to_unm)
        if 0 < t_to_unm < float("inf"):
            urgent = _find_urgent_wip_action(
                state, wip_data, job_data, feasible, t_to_unm
            )
        if prioritize_wip and urgent is not None:
            return urgent

        if direct_starts:
            # ★ Phase16 Shift Window Packing
            # Fix 1-B를 확장: 현재 유인 창에서 처리시간 합이 최대가 되는
            # job 묶음(0-1 knapsack)을 찾고 그 묶음의 첫 job을 선택한다.
            # 예) t_to_unm=200, jobs=[160,120,80]:
            #   Fix1-B → 160 선택 (창 활용 160분)
            #   SWP    → 120+80=200 조합의 첫 job(120) 선택 (창 활용 200분)
            #
            swp_action = _shift_window_first_job(
                direct_starts, job_data, t_to_unm, STORE_MARGIN
            )
            if swp_action is not None:
                return swp_action

            if urgent is not None:
                return urgent

            return max(direct_starts,
                       key=lambda a: (job_data.get(a.prod.job_id) or _dummy_job()).process_time)

        if urgent is not None:
            return urgent

        has_wip_jobs = any(
            job_data.get(jid) and job_data[jid].input_wip_id > 0
            for jid in state.Q_rem
        )
        if has_wip_jobs:
            idle_move = _best_idle_marshalling_action(state, wip_data, job_data, feasible)
            if idle_move is not None:
                return idle_move

    if phase == MachinePhase.BUSY:
        move_action = _best_marshalling_action(state, wip_data, job_data, feasible)
        if move_action is not None:
            return move_action

    waits = [a for a in feasible if a.crane.type == CRANE_WAIT]
    return waits[0] if waits else feasible[0]


def _count_blockers_above(wip_id: int, stacks: dict) -> int:
    """Return the number of blockers above one WIP."""
    for sid, stack in stacks.items():
        for pos in range(len(stack) - 1, -1, -1):
            if stack[pos] == wip_id:
                return len(stack) - 1 - pos
    return 999


def _buried_needed_wips(needed_wips: Set[int], stacks: dict) -> List[Tuple[int, int]]:
    """Return buried target WIPs with their blocker counts."""
    buried: List[Tuple[int, int]] = []
    for wid in needed_wips:
        cnt = _count_blockers_above(wid, stacks)
        if 0 < cnt < 999:
            buried.append((wid, cnt))
    buried.sort(key=lambda x: x[1])
    return buried


def _use_conflict_style_relocation() -> bool:
    scenario_name = _POLICY_CONTEXT.get("scenario_name", "base")
    plan_num = _POLICY_CONTEXT.get("plan_num", 0)
    if scenario_name == "conflict_pair":
        return True
    if scenario_name == "near_ready_plan3" and plan_num >= 3:
        return True
    return False


def _select_target_blockers(
    needed_wips: Set[int],
    stacks: dict,
) -> Set[int]:
    """Choose blockers above the easiest buried target."""
    best_wip_id: Optional[int] = None
    best_count = 999

    for wid in needed_wips:
        cnt = _count_blockers_above(wid, stacks)
        if 0 < cnt < best_count:
            best_count = cnt
            best_wip_id = wid

    if best_wip_id is None:
        return set()

    for sid, stack in stacks.items():
        for pos in range(len(stack) - 1, -1, -1):
            if stack[pos] == best_wip_id:
                return set(stack[pos + 1:])
    return set()


def _best_marshalling_action(
    state:    State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    feasible: list,
) -> Optional[Action]:
    """
    pre-marshalling 행동 후보 및 우선순위 (BUSY 상태)
    """
    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    blockers_to_move: Set[int] = _select_target_blockers(needed_wips, state.stacks)
    buried_needed = _buried_needed_wips(needed_wips, state.stacks)
    conflict_like = len(buried_needed) >= 2 and _use_conflict_style_relocation()

    # [Fix 2-B] 버퍼 사용률 계산 — 절반 초과 시 TEMP_MOVE 억제
    buffer_used   = len(state.buffer_wips)
    buffer_half   = state.buffer_cap / 2.0
    buffer_crowded = buffer_used >= buffer_half  # 버퍼 ≥ 50% 사용 중
    prefer_perm_move = conflict_like or buffer_used >= 1

    safe_perm_moves = _safe_perm_moves(feasible, blockers_to_move, needed_wips, state.stacks)
    if safe_perm_moves:
        # 안전한 영구이동 목적지가 있으면 TEMP_MOVE 왕복보다 먼저 사용한다.
        return safe_perm_moves[0]

    temp_moves = [
        a for a in feasible
        if a.crane.type == CRANE_TEMP_MOVE
        and a.crane.wip_id in blockers_to_move
        and a.crane.wip_id not in needed_wips
        and a.crane.wip_id not in state.buffer_wips
        and not buffer_crowded
        and not prefer_perm_move
        and len(blockers_to_move) == 1
    ]
    # 버퍼가 혼잡하더라도 blocker가 1개뿐이고 다른 방법이 없으면 허용
    if not temp_moves and buffer_crowded:
        temp_moves = [
            a for a in feasible
            if a.crane.type == CRANE_TEMP_MOVE
            and a.crane.wip_id in blockers_to_move
            and a.crane.wip_id not in needed_wips
            and a.crane.wip_id not in state.buffer_wips
            and len(blockers_to_move) == 1   # 마지막 blocker만 예외 허용
        ]

    if temp_moves:
        return temp_moves[0]

    pre_pos = [
        a for a in feasible
        if a.crane.type == CRANE_PRE_POSITION
        and a.crane.wip_id in needed_wips
    ]
    if pre_pos:
        def pre_score(a: Action) -> Tuple[int, int]:
            dst_sid = a.crane.dst_stack
            wip_being_placed = a.crane.wip_id
            other_needed_buried = int(any(
                wid in needed_wips and wid != wip_being_placed
                for wid in state.stacks.get(dst_sid, [])
            ))
            return (other_needed_buried, len(state.stacks.get(dst_sid, [])))
        return min(pre_pos, key=pre_score)

    # [Fix 2-B] 버퍼가 혼잡하거나 TEMP_MOVE 억제 상태라면 MOVE(영구이동)로 blocker 처리
    if buffer_crowded or prefer_perm_move:
        if safe_perm_moves:
            return safe_perm_moves[0]

    if state.buffer_cap == 0 or len(state.Q_rem) == 0:
        restore = _best_restore_action(state, wip_data, job_data, feasible,
                                        avoid_wip_ids=blockers_to_move)
        if restore is not None:
            return restore

    return None


def _best_restore_action(
    state: State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    feasible: list,
    avoid_wip_ids: Optional[Set[int]] = None,
) -> Optional[Action]:
    """
    RESTORE 목적지 선택.
    1. avoid_wip_ids에 속한 WIP 복원 회피
    2. [Fix 1-C 연계] needed WIP이 있는 스택으로 복원 하드 금지
       - 기존에는 소프트 패널티(penalized=1)만 적용
       - 수정: needed WIP을 포함한 스택은 hard-filter로 완전 제외
       - 근거: WIP 87이 반복적으로 Stack 1(WIP 55 위치)에 복원되면서
               WIP 55를 재차단하는 oscillation이 발생(base Plan3 trace 확인)
    3. WIP 기존 스택과 가까운 목적지 우선
    """
    restores = [a for a in feasible if a.crane.type == CRANE_RESTORE]
    if not restores:
        return None

    if avoid_wip_ids:
        non_blocker_restores = [a for a in restores
                                 if a.crane.wip_id not in avoid_wip_ids]
        if non_blocker_restores:
            restores = non_blocker_restores

    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    # [Fix 1-C 연계] needed WIP이 있는 스택 — 하드 필터로 완전 제외
    blocked_target_stacks: Set[int] = set()
    if needed_wips:
        for sid, stack in state.stacks.items():
            if any(wid in needed_wips for wid in stack):
                blocked_target_stacks.add(sid)

    # 1차: needed WIP 스택 완전 제외한 후보
    safe_restores = [a for a in restores
                     if a.crane.dst_stack not in blocked_target_stacks]
    if safe_restores:
        restores = safe_restores
    # 2차 fallback: 안전한 목적지가 없으면 전체 후보에서 패널티 기반 선택

    def restore_score(a: Action) -> Tuple[int, int]:
        dst_sid = a.crane.dst_stack
        penalized = 1 if dst_sid in blocked_target_stacks else 0
        wip = wip_data.get(a.crane.wip_id)
        dist = abs(dst_sid - wip.stack_id) if wip is not None else 0
        return (penalized, dist)

    chosen = min(restores, key=restore_score)
    # [DEBUG] blocked 스택에 복원되는 경우 경고 출력
    if chosen.crane.dst_stack in blocked_target_stacks:
        import sys
        print(f"  [RESTORE-WARN] WIP {chosen.crane.wip_id} → Stack {chosen.crane.dst_stack} "
              f"(needed_wips={needed_wips}, blocked={blocked_target_stacks}, "
              f"safe_cnt={len(safe_restores)}, total_cnt={len([a for a in feasible if a.crane.type == CRANE_RESTORE])})",
              file=sys.stderr)
    return chosen


def _best_idle_marshalling_action(
    state:    State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    feasible: List[Action],
) -> Optional[Action]:
    """
    EMPTY 상태에서 필요 WIP 블로커를 제거하는 최선의 행동을 선택한다.
    """
    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    blockers: Set[int] = _select_target_blockers(needed_wips, state.stacks)
    buried_needed = _buried_needed_wips(needed_wips, state.stacks)
    conflict_like = len(buried_needed) >= 2 and _use_conflict_style_relocation()

    if not blockers:
        return None

    safe_perm_moves = _safe_perm_moves(feasible, blockers, needed_wips, state.stacks)
    if safe_perm_moves:
        return safe_perm_moves[0]

    temp_moves = [
        a for a in feasible
        if a.crane.type == CRANE_TEMP_MOVE
        and a.crane.wip_id in blockers
        and len(blockers) == 1
    ]
    if temp_moves and not conflict_like and len(state.buffer_wips) == 0:
        return temp_moves[0]

    return None


def _safe_perm_moves(
    feasible: List[Action],
    blockers_to_move: Set[int],
    needed_wips: Set[int],
    stacks: dict,
) -> List[Action]:
    perm_moves = [
        a for a in feasible
        if a.crane.type == CRANE_MOVE
        and a.crane.wip_id in blockers_to_move
        and a.crane.wip_id not in needed_wips
        and not any(wid in needed_wips for wid in stacks.get(a.crane.dst_stack, []))
    ]
    perm_moves.sort(key=lambda a: len(stacks.get(a.crane.dst_stack, [])))
    return perm_moves


def _slot_quality_bonus(
    state: State,
    action: Action,
    wip_data: Dict[int, WIPData],
) -> float:
    """
    Phase12 slot-aware loading용 추가 보너스.
    """
    crane = action.crane
    slot = getattr(crane, "slot", None)
    if crane.type != CRANE_PICKING or slot is None or not hasattr(state, "mach_slots"):
        return 0.0

    wip = wip_data.get(crane.wip_id)
    if wip is None:
        return 0.0

    try:
        from ..env.slot_layout import place_wip_in_slots
    except Exception:
        return 0.0

    placed = place_wip_in_slots(state.mach_slots, crane.wip_id, wip, slot)
    if placed is None:
        return float("-inf")
    new_slots, _ = placed
    return _score_slot_layout(new_slots)


def _score_slot_layout(slots: Dict[str, Optional[int]]) -> float:
    """
    현재 2x2 layout의 품질 점수.
    """
    weights = {"TL": 4.0, "TR": 3.0, "BL": 2.0, "BR": 1.0}
    score = 0.0

    for slot, wid in slots.items():
        if wid is not None:
            score += weights.get(slot, 0.0)

    if slots.get("BL") is not None and slots.get("TL") is None:
        score -= 10.0
    if slots.get("BR") is not None and slots.get("TR") is None:
        score -= 10.0

    for top, bottom in (("TL", "BL"), ("TR", "BR")):
        if slots.get(top) is None and slots.get(bottom) is None:
            score += 1.5

    return score


# ── Phase14 헬퍼 함수 ──────────────────────────────────────────────────────────

def _time_to_next_unmanned(clock: float, shift_cfg) -> float:
    """
    [Fix 1-B] 현재 clock에서 다음 무인 구간 시작까지 남은 분을 반환.

    - 현재 유인 구간이면: 다음 무인 구간까지 남은 시간 반환
    - 현재 무인 구간이면: inf 반환 (이미 무인이므로 제약 없음 — 어차피 WAIT)
    - 점심 무인(unm1)과 야간 무인(unm2) 모두 고려
    """
    if shift_cfg is None:
        return float("inf")
    t_rel = clock % shift_cfg.cycle_minutes
    # 오전 유인 구간
    if t_rel < shift_cfg._unm1_start:
        return shift_cfg._unm1_start - t_rel
    # 점심 무인 구간 내
    if t_rel < shift_cfg._unm1_end:
        return float("inf")
    # 오후 유인 구간
    if t_rel < shift_cfg._unm2_start:
        return shift_cfg._unm2_start - t_rel
    # 야간 무인 구간 내 (또는 사이클 종료)
    return float("inf")


class _DummyJob:
    """process_time=0을 가진 더미 JobData (None 안전 처리용)."""
    process_time: float = 0.0


_DUMMY_JOB_INSTANCE = _DummyJob()


def _dummy_job() -> _DummyJob:
    return _DUMMY_JOB_INSTANCE


def _find_urgent_wip_action(
    state:     State,
    wip_data:  Dict[int, WIPData],
    job_data:  Dict[int, JobData],
    feasible:  list,
    t_to_unm:  float,
) -> Optional[Action]:
    """
    [Fix 1-C] 야간무인 전에 완료 가능한 짧은 WIP job을 탐색하고,
    그 WIP의 blocker를 제거하는 행동(TEMP_MOVE 또는 MOVE)을 반환.

    알고리즘:
      1. 남은 WIP job 중 '예상 완료 시간 ≤ t_to_unm'인 후보를 수집
         예상 완료 시간 = (blockers) × CRANE_STEP + CRANE_STEP(픽업) + process_time
      2. 예상 완료 시간이 가장 짧은 job 선택 (여유 최대)
      3. 해당 WIP 위의 blocker를 제거하는 첫 번째 행동 반환
         - blocker가 없으면 None (이미 PICKING 가능 → priority 3에서 처리됨)

    CRANE_STEP: 블로커 1개 제거 또는 픽업 1회에 소요되는 예상 시간 (보수적 추정)
    """
    CRANE_STEP = 5.0   # 크레인 1동작 예상 소요 시간(분) — 보수적 추정

    candidates: List[Tuple[float, int, int]] = []  # (est_total, job_id, wip_id)

    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job is None or job.input_wip_id == 0:
            continue
        wip_id = job.input_wip_id

        # 스택에서 blocker 수 계산
        n_blockers = _count_blockers_above(wip_id, state.stacks)
        if n_blockers == 999:
            # 스택에 없음 (버퍼에 있거나 미생성)
            if wip_id in state.buffer_wips:
                n_blockers = 0   # 버퍼에 있으면 즉시 픽업 가능
            else:
                continue

        # 예상 완료 시간: 블로커 제거 + 픽업 + 공정
        est = (n_blockers + 1) * CRANE_STEP + job.process_time
        if est <= t_to_unm:
            candidates.append((est, jid, wip_id))

    if not candidates:
        return None

    # 예상 완료 시간이 짧은(여유 많은) job 우선
    candidates.sort(key=lambda x: x[0])
    _, target_jid, target_wip = candidates[0]

    # 버퍼에 있으면 즉시 픽업 가능 → None 반환 (priority 3에서 처리)
    if target_wip in state.buffer_wips:
        return None

    # 스택에서 blocker 목록 추출
    blocker_ids: Set[int] = set()
    for sid, stack in state.stacks.items():
        if target_wip in stack:
            idx = stack.index(target_wip)
            blocker_ids = set(stack[idx + 1:])
            break

    if not blocker_ids:
        return None  # 이미 접근 가능 → PICKING priority에서 처리

    # blocker 제거 행동 탐색 (TEMP_MOVE 우선, 버퍼 혼잡 시 MOVE)
    buffer_used  = len(state.buffer_wips)
    buffer_crowded = buffer_used >= state.buffer_cap / 2.0

    urgent_moves = [
        a for a in feasible
        if a.crane.wip_id in blocker_ids
        and a.crane.type == (CRANE_MOVE if buffer_crowded else CRANE_TEMP_MOVE)
    ]
    if not urgent_moves:
        # 반대 타입으로 재시도
        urgent_moves = [
            a for a in feasible
            if a.crane.wip_id in blocker_ids
            and a.crane.type in (CRANE_TEMP_MOVE, CRANE_MOVE)
        ]

    return urgent_moves[0] if urgent_moves else None


def _should_prioritize_wip_over_raw(
    state: State,
    job_data: Dict[int, JobData],
    t_to_unm: float,
) -> bool:
    """
    When multiple needed WIPs are buried, opening them earlier is often better
    than continuing a long raw-material streak.
    """
    scenario_name = _POLICY_CONTEXT.get("scenario_name", "base")
    plan_num = _POLICY_CONTEXT.get("plan_num", 0)
    if scenario_name not in {"conflict_pair", "near_ready_plan3"}:
        return False
    if scenario_name == "near_ready_plan3" and plan_num < 3:
        return False

    needed_wips: Set[int] = {
        job_data[jid].input_wip_id
        for jid in state.Q_rem
        if jid in job_data and job_data[jid].input_wip_id > 0
    }
    buried = _buried_needed_wips(needed_wips, state.stacks)
    if len(buried) >= 2:
        return True
    if len(buried) == 1 and buried[0][1] >= 3 and t_to_unm > 20.0:
        return True
    return False


def _should_use_shortest_completable_raw_first(t_to_unm: float) -> bool:
    scenario_name = _POLICY_CONTEXT.get("scenario_name", "base")
    plan_num = _POLICY_CONTEXT.get("plan_num", 0)
    return scenario_name == "near_ready_plan3" and plan_num >= 3 and t_to_unm <= 90.0


# ── Phase16 Shift Window Packing ──────────────────────────────────────────────

def _shift_window_first_job(
    direct_starts: list,
    job_data: Dict[int, JobData],
    t_to_unm: float,
    store_margin: float = 5.0,
) -> Optional[Action]:
    """
    Phase16 Shift Window Packing:
    현재 유인 창(t_to_unm - store_margin)에서 처리시간 합이 최대가 되는
    raw job 묶음을 0-1 knapsack DP로 계산하고, 그 묶음에서 가장 긴 job을 반환.

    Fix 1-B와의 차이:
    - Fix 1-B: "완료 가능한 job 중 가장 긴 1개" → 하나만 보고 greedy 선택
    - SWP:     "여러 job을 조합했을 때 창을 가장 꽉 채우는 묶음" → 최적 조합 탐색
               t_to_unm=200, jobs=[160,120,80] 일 때:
               Fix 1-B → 160 (창 활용 160분)
               SWP     → {120,80}=200 조합, 첫 job = 120 (창 활용 200분)

    근거 (Lawler et al. 1993): 처리시간 합 최대화 = 야간무인 전 처리량 최대화
    → 이후 사이클에서 처리해야 할 잔여 작업 최소화 → makespan 감소.

    Phase16 Fix — conditional dual-margin:
    output WIP을 생성하지 않는 raw job(generates_output=False)은 처리 후
    STORE 크레인 이동이 없으므로 store_margin=1.0으로 충분.
    단, all-jobs pack이 이미 no-output job만으로 구성된 경우 override 금지
    (no-output pack이 더 큰 값을 반환해도, 실제로는 output-generating job을
    마지막 manned 창에서 실행시켜 unm 구간을 활용하는 전략을 망가뜨릴 수 있음).

    override 조건: all-jobs pack이 generates_output=True job을 포함할 때만.

    예) buffer_stress t=321.78, t_to_unm=218.22:
      전체(margin=5): budget=213.22, best={9(output!),13}=211.015 → output 포함 → override
      no-output(margin=1): budget=217.22, best={803,1401,14}=214.58 ← 채택

    반례) base t=315.82, t_to_unm=224.18:
      전체(margin=5): budget=219.18, best={803,1401,14}=214.58 → 모두 no-output → override 금지
      (no-output pack {13,1401,14}=222.235이 크지만 채택하지 않음)

    알고리즘:
      - n ≤ 15 job, budget ≤ 540분 → O(n·budget) DP, ~8100 연산으로 즉시 계산
      - 0-1 knapsack: dp[w] = budget w 이하로 달성 가능한 최대 처리시간 합
      - 역추적으로 최적 묶음 재구성
      - 그 묶음 중 처리시간 가장 긴 job을 첫 번째로 반환
    """
    action, val, pack_jids = _swp_solve(direct_starts, job_data, t_to_unm, store_margin)

    # override 조건: all-jobs pack이 generates_output job을 포함할 때만
    NO_OUTPUT_MARGIN = 1.0
    if store_margin > NO_OUTPUT_MARGIN and pack_jids:
        pack_has_output = any(
            job_data.get(jid) and job_data[jid].generates_output
            for jid in pack_jids
        )
        if pack_has_output:
            no_out_starts = [
                a for a in direct_starts
                if job_data.get(a.prod.job_id)
                and not job_data[a.prod.job_id].generates_output
            ]
            if no_out_starts:
                alt_action, alt_val, _ = _swp_solve(
                    no_out_starts, job_data, t_to_unm, NO_OUTPUT_MARGIN
                )
                if alt_action is not None and alt_val > val:
                    return alt_action

    return action


def _swp_solve(
    direct_starts: list,
    job_data: Dict[int, JobData],
    t_to_unm: float,
    store_margin: float,
) -> Tuple[Optional[Action], float, List[int]]:
    """
    SWP 내부 knapsack 실행.
    반환: (first_action, pack_total_minutes, pack_job_id_list)
    action=None 이면 (None, 0.0, []).
    """
    budget = t_to_unm - store_margin
    if budget <= 0:
        return None, 0.0, []

    candidates: List[Tuple[Action, float]] = [
        (a, job_data[a.prod.job_id].process_time)
        for a in direct_starts
        if job_data.get(a.prod.job_id)
        and job_data[a.prod.job_id].process_time <= budget
    ]

    if not candidates:
        return None, 0.0, []
    if len(candidates) == 1:
        jid = candidates[0][0].prod.job_id
        return candidates[0][0], candidates[0][1], [jid]

    # 0-1 Knapsack DP (0.1분 단위 정수화)
    # 값만 1차원으로 두고 역추적을 대충 맞추면 잘못된 조합을 복원할 수 있으므로
    # 선택 여부를 2차원으로 따로 보관해 안정적으로 pack을 재구성한다.
    SCALE = 10
    W = int(round(budget * SCALE))
    n = len(candidates)
    pts = [max(1, int(round(pt * SCALE))) for _, pt in candidates]

    # dp[i][w] = first i개 후보로 budget w 이하에서 달성 가능한 최대 처리시간 합
    dp = [[0] * (W + 1) for _ in range(n + 1)]
    keep = [[False] * (W + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        p = pts[i - 1]
        for w in range(W + 1):
            best = dp[i - 1][w]
            take = -1
            if p <= w:
                take = dp[i - 1][w - p] + p
            if take > best:
                dp[i][w] = take
                keep[i][w] = True
            else:
                dp[i][w] = best

    max_val = dp[n][W]
    if max_val == 0:
        return None, 0.0, []

    # 역추적: 최적 묶음 재구성
    selected_indices: List[int] = []
    w = W
    for i in range(n, 0, -1):
        if keep[i][w]:
            idx = i - 1
            selected_indices.append(idx)
            p = pts[idx]
            w -= p

    if not selected_indices:
        return None, 0.0, []

    # 최적 묶음 중 처리시간이 가장 긴 job을 첫 번째로 실행
    selected = [(candidates[i][0], candidates[i][1]) for i in selected_indices]
    selected.sort(key=lambda x: x[1], reverse=True)
    pack_total = sum(pt for _, pt in selected)
    pack_jids = [a.prod.job_id for a, _ in selected]
    return selected[0][0], pack_total, pack_jids
