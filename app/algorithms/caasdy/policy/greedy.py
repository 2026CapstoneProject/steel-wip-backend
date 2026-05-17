"""
Greedy 정책 (DIDPPy 미설치 시 fallback)

우선순위:
  1. BLOCKED → 바로 STORE
  2. LOADING → batch가 꽉 찼으면 START_PROCESS
  3. 접근 가능한 WIP이 있고 남은 run에 맞으면 PICKING
  4. LOADING 상태이면 START_PROCESS (이미 최소 1개 로드됨)
  5. BUSY 상태:
     5a. 필요 WIP을 가로막는 blocker가 있으면 TEMP_MOVE (버퍼 여유 있을 때)
     5b. blocker가 있으면 MOVE (영구 재배치)
     5c. 버퍼에 다음 job input_wip이 있으면 PRE_POSITION (신규)
         → RESTORE보다 먼저, 전략적 스택에 선배치
     5d. 버퍼에 WIP이 있으면 RESTORE (방어적 버퍼 복원)
     5e. 그 외 → WAIT
  6. 그 외 → WAIT

핵심 아이디어:
  - 출력재가 후속 입력을 열어주지 않으므로 downstream unlock 보너스를 제거한다.
  - 대신 현재 run의 적재율(fill)과 접근성(accessibility)에 더 집중한다.
  - BUSY 중 버퍼에 다음 run의 input_wip이 있으면, RESTORE보다 PRE_POSITION을 먼저 실행한다.
"""

from typing import Dict, List, Optional, Set, Tuple

from ..data.loader import WIPData, JobData
from ..env.state import State, MachinePhase
from ..env.actions import Action, CRANE_PICKING, CRANE_STORE, PROD_START, PROD_DIRECT_START, CRANE_WAIT
from ..env.actions import CRANE_MOVE, CRANE_TEMP_MOVE, CRANE_RESTORE, CRANE_PRE_POSITION
from ..env.feasibility import get_feasible_actions


def greedy_policy(
    state:     State,
    wip_data:  Dict[int, WIPData],
    job_data:  Dict[int, JobData],
) -> Action:
    """
    Greedy 정책: feasible 행동 목록에서 우선순위에 따라 하나를 선택한다.
    """
    feasible = get_feasible_actions(state, wip_data, job_data)
    if not feasible:
        from ..env.actions import WAIT_NONE
        return WAIT_NONE

    phase = state.phase

    #  우선순위 1: BLOCKED → STORE
    if phase == MachinePhase.BLOCKED:
        stores = [a for a in feasible if a.crane.type == CRANE_STORE]
        if stores:
            return stores[0]

    #  우선순위 2: LOADING + batch 꽉 참 → START_PROCESS
    if phase == MachinePhase.LOADING and state.j_mach is not None:
        q = state.j_mach
        job = job_data.get(q)
        if job and len(state.K_mach) >= job.batch_count:
            starts = [a for a in feasible if a.prod.type == PROD_START]
            if starts:
                return starts[0]

    #  우선순위 3: PICKING 탐색
    # EMPTY에서도 "어떤 WIP + 어떤 job 조합으로 시작할지" 점수화한다.
    pickings = [a for a in feasible if a.crane.type == CRANE_PICKING]
    if pickings:
        def picking_score(a: Action) -> float:
            job = job_data.get(a.crane.job_id)
            wip = wip_data.get(a.crane.wip_id)
            if wip is None or job is None:
                return float("-inf")

            short_fill = min(1.0, wip.short_side / max(job.cap_short, 1.0))
            long_fill = min(1.0, wip.long_side / max(job.cap_long, 1.0))
            # Phase 5: PICKING 대상은 unique job (input_wip_id>0)만 해당.
            # 원자재 job (input_wip_id==0)은 DIRECT_START로 처리되므로 PICKING 후보에 없음.
            return 10.0 * short_fill + 6.0 * long_fill
        return max(pickings, key=picking_score)

    #  우선순위 3.2: EMPTY + 버퍼 잔류 → 비필요(non-needed) WIP 복원
    # 접근 가능한 PICKING 후보가 하나라도 있으면 복원보다 PICKING이 우선이다.
    # 그렇지 않으면 TEMP_MOVE 직후 바로 RESTORE가 발생해
    # 기대 순서(TEMP_MOVE -> PICKING -> RESTORE)를 깨뜨린다.
    if phase == MachinePhase.EMPTY and len(state.buffer_wips) > 0:
        needed_unique: Set[int] = set()
        for jid in state.Q_rem:
            jb = job_data.get(jid)
            if jb and jb.input_wip_id > 0:
                needed_unique.add(jb.input_wip_id)
        non_needed_in_buf = [w for w in state.buffer_wips if w not in needed_unique]
        if non_needed_in_buf or len(state.Q_rem) == 0:
            restore = _best_restore_action(
                state, wip_data, job_data, feasible, avoid_wip_ids=needed_unique
            )
            if restore is not None:
                return restore

    #  우선순위 3.5: LOADING + 버퍼 잔류 WIP → RESTORE (TEMP_MOVE 원상복구)
    # 단, 추가 PICKING 후보가 더 이상 없을 때만 복원한다.
    # 그렇지 않으면 첫 PICKING 직후 blocker를 너무 일찍 되돌려
    # 다음 needed WIP 피킹 전에 다시 스택을 막는 비정상 순서가 생긴다.
    if phase == MachinePhase.LOADING and len(state.buffer_wips) > 0:
        restore = _best_restore_action(state, wip_data, job_data, feasible)
        if restore is not None:
            return restore

    #  우선순위 4: START_PROCESS
    if phase == MachinePhase.LOADING and len(state.K_mach) >= 1:
        starts = [a for a in feasible if a.prod.type == PROD_START]
        if starts:
            return starts[0]

    #  우선순위 4.5: EMPTY + PICKING 없음 → DIRECT_START 또는 idle marshalling
    if phase == MachinePhase.EMPTY and not pickings:
        # 4.5a: unique-WIP job이 ≤ NEAR_THRESHOLD 번 이동으로 접근 가능하면
        #        DIRECT_START(특히 장시간)보다 idle 마셜링을 먼저 수행한다.
        #        → 짧은 크레인 작업으로 unique job에 빠르게 접근할 수 있을 때
        #          장시간 원자재 job으로 설비를 묶는 것을 방지한다.
        NEAR_THRESHOLD = 2  # blocker 수 이내면 "가까운" 것으로 판단

        needed_unique: Set[int] = set()
        for jid in state.Q_rem:
            jb = job_data.get(jid)
            if jb and jb.input_wip_id > 0:
                needed_unique.add(jb.input_wip_id)

        min_blockers = 999
        for wid in needed_unique:
            cnt = _count_blockers_above(wid, state.stacks)
            if 0 < cnt < min_blockers:
                min_blockers = cnt

        if min_blockers <= NEAR_THRESHOLD:
            # unique-WIP에 빠르게 접근 가능 → 먼저 마셜링
            idle_move = _best_idle_marshalling_action(state, wip_data, job_data, feasible)
            if idle_move is not None:
                return idle_move

        # 4.5b: 원자재 job DIRECT_START (야드 조작 불필요)
        direct_starts = [a for a in feasible if a.prod.type == PROD_DIRECT_START]
        if direct_starts:
            # process_time이 짧은 job 우선
            def ds_score(a: Action) -> float:
                job = job_data.get(a.prod.job_id)
                return job.process_time if job else float("inf")
            return min(direct_starts, key=ds_score)

        # 4.5c: 블로커 제거해 다음 LOAD를 열어준다
        # (무인가공 시간대는 feasibility의 is_unm 가드로 이미 차단됨)
        idle_move = _best_idle_marshalling_action(state, wip_data, job_data, feasible)
        if idle_move is not None:
            return idle_move

    #  우선순위 5: BUSY 중 pre-marshalling
    if phase == MachinePhase.BUSY:
        move_action = _best_marshalling_action(state, wip_data, job_data, feasible)
        if move_action is not None:
            return move_action

    #  우선순위 6: WAIT
    waits = [a for a in feasible if a.crane.type == CRANE_WAIT]
    return waits[0] if waits else feasible[0]


def _count_blockers_above(wip_id: int, stacks: dict) -> int:
    """스택에서 wip_id 위에 쌓인 WIP 수 반환. 없으면 999(접근 불가)."""
    for sid, stack in stacks.items():
        for pos in range(len(stack) - 1, -1, -1):
            if stack[pos] == wip_id:
                return len(stack) - 1 - pos
    return 999  # 버퍼에 있거나 이미 픽킹된 경우


def _select_target_blockers(
    needed_wips: Set[int],
    stacks: dict,
) -> Set[int]:
    """
    needed_wip 중 현재 스택에서 blocker 수가 가장 적은 WIP을 선택,
    그 WIP 위에 있는 blocker 집합만 반환한다.

    → 가장 빨리 unblock 가능한 job에 집중 굴착 (unguided digging 방지).
    """
    best_wip_id: Optional[int] = None
    best_count = 999

    for wid in needed_wips:
        cnt = _count_blockers_above(wid, stacks)
        if 0 < cnt < best_count:   # cnt==0이면 이미 accessible → PICKING이 처리
            best_count = cnt
            best_wip_id = wid

    if best_wip_id is None:
        return set()

    # best_wip_id 위에 있는 WIP만 blocker로 수집
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
    pre-marshalling 행동 후보 및 우선순위

    우선순위:
      1. TEMP_MOVE — blocker를 버퍼로 임시 이동 (가장 빠른 차단 해소)
         ※ BUSY 상태에서는 MOVE(영구 재배치) 금지 — oscillation 방지
      2. PRE_POSITION
                   — 버퍼의 needed_wip을 최적 스택에 선배치
                     (RESTORE보다 먼저: 전략적 위치 선점)
      3. RESTORE   — 버퍼 WIP 범용 복원 (방어적, 버퍼 포화 또는 잔여 job 없을 때)

    개선: 모든 needed WIP 블로커를 합집합 처리하지 않고,
         blocker 수가 가장 적은 needed WIP에 집중 굴착한다.
    """
    # 다음에 필요한 WIP 집합
    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    #  0. RESTORE — 방금 처리 완료한 스택의 blocker는 먼저 제자리 복귀
    # 서비스 운영 기준에서는 다른 스택 블로커를 추가로 TEMP_MOVE 하기 전에
    # 이미 해제된 스택(A-2)의 blocker(예: QR-99)를 바로 복구하는 편이 자연스럽다.
    # feasibility가 restore 후보를 열어준 상태라면, 여기서 TEMP_MOVE보다 우선한다.
    restore_first = _best_restore_action(state, wip_data, job_data, feasible)
    if restore_first is not None:
        restore_wip = wip_data.get(restore_first.crane.wip_id)
        if restore_wip is not None and restore_wip.stack_id > 0:
            still_needed_on_origin = any(
                target_wip in state.stacks.get(restore_wip.stack_id, [])
                for target_wip in needed_wips
            )
            if not still_needed_on_origin:
                return restore_first

    #  1. blocker 탐색 (TEMP_MOVE / MOVE)
    # 가장 빨리 unblock 가능한 needed WIP의 blocker만 선택
    blockers_to_move: Set[int] = _select_target_blockers(needed_wips, state.stacks)

    # TEMP_MOVE 우선 (버퍼 여유 있을 때)
    temp_moves = [
        a for a in feasible
        if a.crane.type == CRANE_TEMP_MOVE
        and a.crane.wip_id in blockers_to_move
    ]
    if temp_moves:
        # ── Oscillation 방지 ─────────────────────────────────────────────────
        # 버퍼 잔여 공간(buffer_cap)이 블로커 수보다 적으면 TEMP_MOVE를 실행해도
        # 버퍼가 즉시 포화 → RESTORE → TEMP_MOVE → RESTORE … 의 무한 루프가 생긴다.
        #
        # TEMP_MOVE를 허용하는 조건:
        #   (a) 이번 TEMP_MOVE로 needed WIP이 직접 접근 가능해진다
        #       (blocker가 정확히 1개 = 마지막 장애물)
        #   (b) 버퍼에 모든 blocker를 한꺼번에 수용할 공간이 있다
        #       → 중간에 RESTORE 없이 순서대로 굴착 가능
        # ─────────────────────────────────────────────────────────────────────
        will_make_progress = (len(blockers_to_move) == 1)
        can_fit_all        = (state.buffer_cap >= len(blockers_to_move))
        if will_make_progress or can_fit_all:
            return temp_moves[0]
        # 조건 불충족: TEMP_MOVE 스킵 → PRE_POSITION / RESTORE / WAIT 으로 처리

    # BUSY 상태에서는 MOVE(영구 재배치)를 허용하지 않는다.
    # 이유: 모든 스택에 needed WIP이 있을 경우 어느 목적지를 선택해도
    #       크기가 비슷한 두 스택 사이에서 반복 이동(oscillation)이 발생한다.
    #       BUSY 중 pre-marshalling은 TEMP_MOVE(버퍼)만 사용하고,
    #       버퍼가 꽉 찼으면 PRE_POSITION / RESTORE / WAIT으로 처리한다.

    #  2. PRE_POSITION — 버퍼의 needed_wip 전략 선배치
    pre_pos = [
        a for a in feasible
        if a.crane.type == CRANE_PRE_POSITION
        and a.crane.wip_id in needed_wips
    ]
    if pre_pos:
        # ── 스코어링 ──────────────────────────────────────────────────────────
        # 1순위: 배치 대상 스택에 다른 needed WIP이 묻혀 있지 않아야 한다.
        #        → PRE_POSITION 직후 해당 WIP이 다른 WIP의 blocker가 되는 사태 방지.
        #          (WIP 102를 Stack 2에 놓았더니 Stack 2의 WIP 40이 막혀서
        #           즉시 TEMP_MOVE(102)가 발생하는 oscillation을 방지)
        # 2순위: 스택 크기 최소 (최상단 즉시 노출 보장)
        # ────────────────────────────────────────────────────────────────────
        def pre_score(a: Action) -> Tuple[int, int]:
            dst_sid = a.crane.dst_stack
            wip_being_placed = a.crane.wip_id
            # 해당 스택에 배치할 WIP 자신을 제외한 다른 needed WIP이 있으면 페널티
            other_needed_buried = int(any(
                wid in needed_wips and wid != wip_being_placed
                for wid in state.stacks.get(dst_sid, [])
            ))
            return (other_needed_buried, len(state.stacks.get(dst_sid, [])))
        return min(pre_pos, key=pre_score)

    #  3. RESTORE — 방어적 버퍼 복원
    # 버퍼가 꽉 찼거나, 남은 job이 없을 때만 RESTORE를 적극 수행한다.
    # 그렇지 않으면 불필요한 복원으로 future blocker를 다시 만들 수 있어 WAIT이 낫다.
    # ★ blockers_to_move를 avoid_wip_ids로 전달 — 방금 TEMP_MOVE한 blocker를 다시
    #   복원하지 않도록 한다. non-blocker WIP(출력재 등)을 먼저 복원한다.
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

    원칙:
      0. (최우선) avoid_wip_ids에 속한 WIP은 복원 대상에서 제외한다.
         — BUSY 중 blockers_to_move에 포함된 WIP은 버퍼에 둬야 하므로
           버퍼에 non-blocker WIP이 있으면 그쪽을 먼저 복원한다.
           (avoid 대상만 남았으면 어쩔 수 없이 그 중에서 선택)
      1. (우선) input WIP이 묻혀 있는 스택 위로는 되도록 복원하지 않는다.
      2. (보조) 해당 WIP의 기존(초기) 스택과 최대한 가까운 스택으로 복원한다.
         거리 = |dst_stack_id - wip.stack_id| (스택 ID 번호 차이로 근사)
    """
    restores = [a for a in feasible if a.crane.type == CRANE_RESTORE]
    if not restores:
        return None

    # 0순위: blocker WIP은 버퍼에 유지 — non-blocker를 먼저 복원한다.
    if avoid_wip_ids:
        non_blocker_restores = [a for a in restores
                                 if a.crane.wip_id not in avoid_wip_ids]
        if non_blocker_restores:
            restores = non_blocker_restores
        # non-blocker가 없으면 avoid 대상이라도 복원 (fallback)

    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    blocked_target_stacks: Set[int] = set()
    if needed_wips:
        for sid, stack in state.stacks.items():
            if any(wid in needed_wips for wid in stack):
                blocked_target_stacks.add(sid)

    def restore_score(a: Action) -> Tuple[int, int]:
        dst_sid = a.crane.dst_stack
        # 1순위: input WIP 스택 위 복원 회피
        penalized = 1 if dst_sid in blocked_target_stacks else 0
        # 2순위: 해당 WIP의 기존 스택과 가장 가까운 목적지 우선
        wip = wip_data.get(a.crane.wip_id)
        dist = abs(dst_sid - wip.stack_id) if wip is not None else 0
        return (penalized, dist)

    return min(restores, key=restore_score)


def _best_idle_marshalling_action(
    state:    State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    feasible: List[Action],
) -> Optional[Action]:
    """
    EMPTY 상태에서 필요 WIP 블로커를 제거하는 최선의 행동을 선택한다.

    우선순위:
      1. TEMP_MOVE — 버퍼로 임시 이동 (버퍼 여유 있을 때, 나중에 RESTORE 가능)
      2. MOVE      — 다른 스택으로 영구 이동 (가장 짧은 스택으로)

    원자재 job (input_wip_id==0)은 야드 WIP을 사용하지 않으므로
    Unique job (input_wip_id > 0)의 needed_wip 위에 쌓인 WIP만 blocker로 탐색한다.

    블로커가 없거나 feasible에 해당 행동이 없으면 None 반환.
    """
    needed_wips: Set[int] = set()
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job and job.input_wip_id > 0:
            needed_wips.add(job.input_wip_id)

    # 가장 빨리 unblock 가능한 needed WIP의 blocker만 집중 굴착
    blockers: Set[int] = _select_target_blockers(needed_wips, state.stacks)

    if not blockers:
        return None

    # TEMP_MOVE 우선 (버퍼로 임시 → 비용 C_TEMP, 나중에 복원 가능)
    temp_moves = [
        a for a in feasible
        if a.crane.type == CRANE_TEMP_MOVE and a.crane.wip_id in blockers
    ]
    if temp_moves:
        return temp_moves[0]

    # MOVE (영구 재배치) — needed WIP이 없는 안전한 스택으로만 허용
    moves = [
        a for a in feasible
        if a.crane.type == CRANE_MOVE and a.crane.wip_id in blockers
    ]
    if moves:
        def _idle_move_score(a: Action) -> Tuple[int, int]:
            dst_sid = a.crane.dst_stack
            # 1순위: needed WIP이 없는 스택 (dump해도 새 blocker 안 생김)
            has_needed = int(any(wid in needed_wips
                                 for wid in state.stacks.get(dst_sid, [])))
            # 2순위: 스택 크기 최소
            return (has_needed, len(state.stacks.get(dst_sid, [])))
        best_move = min(moves, key=_idle_move_score)
        # 안전한 목적지(needed WIP 없음)가 존재할 때만 MOVE 실행.
        # 모든 스택에 needed WIP이 있으면 MOVE를 건너뛴다.
        # → 크기 차이로 인한 stack2↔stack3 oscillation 방지.
        dst_sid = best_move.crane.dst_stack
        safe = not any(wid in needed_wips for wid in state.stacks.get(dst_sid, []))
        if safe:
            return best_move
        # 안전한 목적지 없음 → TEMP_MOVE도 없으므로 None 반환 (WAIT 또는 DIRECT_START로 진행)
