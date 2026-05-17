"""
  - input_wip_id == 0 인 run은 모두 원자재(raw material) job.
    인벤토리 WIP을 LOAD하지 않고 DIRECT_START만 허용.
    동일 규격 원자재가 여러 장 존재할 수 있으므로 인벤토리 체크 없이 항상 실행 가능.
  - generates_output=True 런의 출력재(is_output_wip=True)는 PICKING 후보에서 영구 제외.
  - 서비스 운영 모드에서는 EMPTY 상태에서 PICKING 후보가 없을 때만 DIRECT_START 추가.
  - idle/busy marshalling의 generic job 블로커 탐색 제거 (원자재 run은 야드 WIP 불필요).
    서비스 운영 모드에서는 PRE_POSITION 비활성화
"""

from typing import Dict, List, Set
from ..data.params import STACK_TO_NODE
from ..data.loader import WIPData, JobData
from .state import State, MachinePhase
from .actions import (
    Action, CraneAction, ProdAction,
    CRANE_PICKING, CRANE_STORE, CRANE_MOVE, CRANE_TEMP_MOVE, CRANE_RESTORE,
    CRANE_PRE_POSITION, CRANE_WAIT,
    PROD_START, PROD_DIRECT_START, PROD_CONTINUE, PROD_NONE,
    WAIT_NONE, WAIT_CONTINUE,
)

# ── Phase 10: 이종 WIP 동시 투입 (co-loading) 모드 플래그 ────────────────────
# Phase10/run.py에서 set_co_loading(True)로 활성화
# 기본값 False → Phase7~9와 동일한 동작 유지
_co_loading_mode: bool = False


def _has_remaining_inventory_jobs(
    state: State,
    job_data: Dict[int, JobData],
) -> bool:
    """
    아직 시작 전인 job 중 야드 WIP(input_wip_id>0)를 필요로 하는 항목이 남아 있는지.

    서비스 운영 모드에서는 이 값이 True인 동안
    DIRECT_START나 버퍼 unload 성격의 RESTORE를 최대한 억제해
    기대 순서(TEMP_MOVE -> PICKING -> RESTORE)를 보존한다.
    """
    return any(
        jid in job_data
        and job_data[jid].input_wip_id > 0
        for jid in state.Q_rem
    )


def _stack_has_unserved_target_job(
    state: State,
    job_data: Dict[int, JobData],
    wip_data: Dict[int, WIPData],
    stack_id: int,
) -> bool:
    """
    주어진 stack에 대해, 아직 야드에서 꺼내야 하는 target job이 남아 있으면 True.

    이미 K_mach에 올라간 input_wip은 해당 스택 접근이 끝난 것으로 간주한다.
    이 판단을 이용해 다른 스택 job이 남아 있어도
    "방금 비운 스택의 blocker는 바로 복구"를 허용한다.
    """
    for jid in state.Q_rem:
        job = job_data.get(jid)
        if job is None or job.input_wip_id <= 0:
            continue
        if job.input_wip_id in state.K_mach:
            continue
        target_wip = wip_data.get(job.input_wip_id)
        if target_wip is None:
            continue
        if target_wip.stack_id == stack_id:
            return True
    return False


def _top_buffer_restore_allowed(
    state: State,
    job_data: Dict[int, JobData],
    wip_data: Dict[int, WIPData],
) -> bool:
    """
    버퍼 top WIP를 지금 복구해도 되는지 판단한다.

    기준:
      - 버퍼가 비어 있지 않아야 함
      - top WIP의 원래 stack(초기 stack_id)에 아직 미처리 target job이 남아 있지 않아야 함
    """
    top_buf = state.top_buffer_wip()
    if top_buf is None:
        return False
    top_wip = wip_data.get(top_buf)
    if top_wip is None or top_wip.stack_id <= 0:
        return False
    return not _stack_has_unserved_target_job(
        state=state,
        job_data=job_data,
        wip_data=wip_data,
        stack_id=top_wip.stack_id,
    )


def set_co_loading(enabled: bool) -> None:
    """이종 WIP 동시 투입(co-loading) 모드 전환 (Phase10에서 호출)"""
    global _co_loading_mode
    _co_loading_mode = enabled


# 공개 API

def get_feasible_actions(
    state: State,
    wip_data:  Dict[int, WIPData],
    job_data:  Dict[int, JobData],
) -> List[Action]:
    """
    현재 상태 S_t에서 실행 가능한 행동 목록을 반환한다.
    빈 리스트가 반환되는 일은 없다 — 항상 WAIT이 포함된다.
    행동 생성 규칙:
      - BUSY: MOVE / TEMP_MOVE / RESTORE 허용 (pre-marshalling)
      - EMPTY: PICKING 없을 때 블로커 MOVE / TEMP_MOVE 허용 (idle marshalling)
               단, 무인가공 시간대는 is_unm 가드로 막혀 크레인 정지 유지
      - EMPTY/LOADING: 버퍼 WIP도 PICKING 대상
      - EMPTY에서 generic job(input_wip_id == 0)의 첫 PICKING 후보를 탐색
      - same_spec + job template 체크 정밀화
    """
    phase = state.phase

    # 무인가공 시간대 → crane=WAIT만 허용 (크레인 정지, 설비만 가동)
    if state.is_unm():
        if phase == MachinePhase.BUSY:
            return [WAIT_CONTINUE]
        if phase == MachinePhase.LOADING:
            acts: List[Action] = []
            _add_start_process_actions(state, job_data, acts)
            acts.append(WAIT_NONE)
            return acts
        return [WAIT_NONE]

    actions: List[Action] = []
    has_remaining_inventory_jobs = _has_remaining_inventory_jobs(state, job_data)

    if phase == MachinePhase.BLOCKED:
        _add_store_actions(state, actions)
        actions.append(WAIT_NONE)
        return actions

    # Phase 2: MOVE / TEMP_MOVE / RESTORE 추가
    if phase == MachinePhase.BUSY:
        _add_marshalling_actions(state, wip_data, job_data, actions)
        actions.append(WAIT_CONTINUE)
        return actions

    if phase in (MachinePhase.EMPTY, MachinePhase.LOADING):
        _add_picking_actions(state, wip_data, job_data, actions)
        _add_start_process_actions(state, job_data, actions)
        if (
            phase == MachinePhase.LOADING
            and state.buffer_wips
            and _top_buffer_restore_allowed(state, job_data, wip_data)
        ):
            # PICKING 완료 후 버퍼 WIP 즉시 복원 허용 (TEMP_MOVE 원상복구)
            # LOADING 중에는 기계가 아직 미가동 → prod=PROD_NONE
            top_buf = state.top_buffer_wip()
            if top_buf is not None:
                for dst_sid in state.stacks.keys():
                    actions.append(Action(
                        crane=CraneAction(
                            type=CRANE_RESTORE,
                            wip_id=top_buf,
                            dst_stack=dst_sid,
                        ),
                        prod=ProdAction(PROD_NONE),
                    ))
        if phase == MachinePhase.EMPTY:
            has_picking_candidate = any(
                a.crane.type == CRANE_PICKING for a in actions
            )
            if (
                not has_picking_candidate
                and (
                    not has_remaining_inventory_jobs
                    or _top_buffer_restore_allowed(state, job_data, wip_data)
                )
            ):
                _add_cleanup_restore_actions(state, actions)
            # 서비스 운영 모드:
            # 접근 가능한 PICKING 후보가 하나라도 있으면 DIRECT_START를 만들지 않는다.
            # 그렇지 않으면 raw job이 기존 야드 WIP 피킹보다 앞서 잡혀
            # 현장 기준으로 부자연스러운 순서가 생길 수 있다.
            _add_direct_start_actions(
                state,
                job_data,
                actions,
                allow_direct_start=not has_remaining_inventory_jobs,
            )
            # LOAD도 없고 DIRECT_START도 없으면 블로커 제거 (매몰 WIP 발굴)
            any_productive = any(
                a.crane.type == CRANE_PICKING or a.prod.type == PROD_DIRECT_START
                for a in actions
            )
            if not any_productive:
                _add_idle_marshalling_actions(state, wip_data, job_data, actions)

    # WAIT은 항상 추가
    if phase == MachinePhase.BUSY:
        actions.append(WAIT_CONTINUE)
    else:
        actions.append(WAIT_NONE)

    return actions if actions else [WAIT_NONE]


# 재배치 행동 (BUSY 중 pre-marshalling)

def _add_marshalling_actions(
    state:    State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    out:      List[Action],
) -> None:
    """
    BUSY 상태에서의 재배치 행동 후보(Pre-marshalling): MOVE / TEMP_MOVE / PRE_POSITION / RESTORE

    MOVE(k, src, dst)        : WIP k를 src 스택에서 dst 스택으로 영구 이동 (C^rel 비용)
    TEMP_MOVE(k, src)        : WIP k를 버퍼로 임시 이동 (C^temp 비용, buffer_cap >= 1)
    PRE_POSITION(k, dst)     : 버퍼 WIP k를 미래 PICKING 최적 스택으로 선배치 (신규)
                               조건: k가 Q_rem의 어떤 run의 input_wip_id일 때만 허용
                               대상 스택: WIP 수 최소 스택 (최상단 즉시 노출 보장)
    RESTORE(k, dst)          : 버퍼 WIP k를 임의 스택으로 복원 (방어적, 비용 0)

    대상 WIP 조건 (MOVE/TEMP_MOVE):
      - yard stack의 최상단 (top_kt = 1) — 직접 접근 가능
      - K_mach에 없음 (설비 위 WIP은 이동 불가)
    """
    accessible = state.accessible_wips()  # {stack_id → top_wip_id}

    # Q_rem의 input_wip 집합 (PRE_POSITION 대상 판별용)
    needed_wips: Set[int] = {
        job_data[jid].input_wip_id
        for jid in state.Q_rem
        if jid in job_data and job_data[jid].input_wip_id > 0
    }

    for src_sid, wip_id in accessible.items():
        if wip_id in state.K_mach:
            continue

        # MOVE → 다른 스택으로 영구 이동
        for dst_sid in state.stacks.keys():
            if dst_sid == src_sid:
                continue
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_MOVE,
                    wip_id=wip_id,
                    src_stack=src_sid,
                    dst_stack=dst_sid,
                ),
                prod=ProdAction(PROD_CONTINUE),
            ))

        # TEMP_MOVE → 버퍼로 임시 이동 (버퍼 여유 있을 때만)
        if state.buffer_cap >= 1:
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_TEMP_MOVE,
                    wip_id=wip_id,
                    src_stack=src_sid,
                ),
                prod=ProdAction(PROD_CONTINUE),
            ))

    # 서비스 운영 모드에서는 PRE_POSITION을 비활성화한다.
    # 실험에서는 장기 관점 선배치로 해석될 수 있지만,
    # 서비스 배치 결과에서는 의미 없는 왕복처럼 보이고
    # 기대 순서(TEMP_MOVE -> PICKING -> RESTORE)를 강하게 깨뜨린다.

    # 버퍼 내 WIP을 yard 스택으로 복원 (방어적 — 버퍼 공간 확보)
    top_buf = state.top_buffer_wip()
    if top_buf is not None and _top_buffer_restore_allowed(state, job_data, wip_data):
        for dst_sid in state.stacks.keys():
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_RESTORE,
                    wip_id=top_buf,
                    dst_stack=dst_sid,
                ),
                prod=ProdAction(PROD_CONTINUE),
            ))


def _add_cleanup_restore_actions(
    state: State,
    out: List[Action],
) -> None:
    """
    EMPTY 상태에서 버퍼 잔류 WIP를 야드로 복원하는 cleanup 행동을 추가한다.
    """
    top_buf = state.top_buffer_wip()
    if top_buf is not None:
        for dst_sid in state.stacks.keys():
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_RESTORE,
                    wip_id=top_buf,
                    dst_stack=dst_sid,
                ),
                prod=ProdAction(PROD_NONE),
            ))


def _add_idle_marshalling_actions(
    state:    State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    out:      List[Action],
) -> None:
    """

    LOAD도 없고 DIRECT_START도 없을 때만 호출되며, 무인가공 시간대는 is_unm 가드로 차단됨.
    prod는 PROD_NONE (설비 미가동 상태).

    원자재 job (input_wip_id==0)은 야드 WIP을 사용하지 않으므로 블로커 탐색 제외.
    Unique job (input_wip_id > 0)의 needed_wip 위에 쌓인 WIP만 blocker로 간주한다.
    """
    # unique run의 needed_wip 집합
    needed_wips: Set[int] = {
        job_data[jid].input_wip_id
        for jid in state.Q_rem
        if jid in job_data and job_data[jid].input_wip_id > 0
    }
    blockers: Set[int] = set()
    for sid, stack in state.stacks.items():
        for pos in range(len(stack) - 1, -1, -1):
            wid = stack[pos]
            if wid in needed_wips:
                for above_pos in range(pos + 1, len(stack)):
                    blockers.add(stack[above_pos])
                break

    if not blockers:
        return

    accessible = state.accessible_wips()  # {stack_id → top_wip_id}

    for src_sid, wip_id in accessible.items():
        if wip_id not in blockers:
            continue  # blocker가 아닌 WIP은 건드리지 않음

        # TEMP_MOVE → 버퍼로 임시 이동 (버퍼 여유 있을 때만)
        if state.buffer_cap >= 1:
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_TEMP_MOVE,
                    wip_id=wip_id,
                    src_stack=src_sid,
                ),
                prod=ProdAction(PROD_NONE),
            ))

        # MOVE → 다른 스택으로 영구 이동
        for dst_sid in state.stacks.keys():
            if dst_sid == src_sid:
                continue
            out.append(Action(
                crane=CraneAction(
                    type=CRANE_MOVE,
                    wip_id=wip_id,
                    src_stack=src_sid,
                    dst_stack=dst_sid,
                ),
                prod=ProdAction(PROD_NONE),
            ))


# 내부: PICKING 후보 생성 (버퍼 WIP 포함, same_spec 강화)

def _add_picking_actions(
    state: State,
    wip_data:  Dict[int, WIPData],
    job_data:  Dict[int, JobData],
    out:       List[Action],
) -> None:
    """
    PICKING(k, src_stack, job_id) 후보를 out에 추가한다.

    변경:
      - 야드 top WIP뿐 아니라 버퍼 내 WIP도 PICKING 대상에 포함
      - generic job(input_wip_id == 0)에 대해 첫 PICKING 후보를 탐색
      - same_spec 체크 정밀화 (_compat_p4 사용)
    """
    accessible = state.accessible_wips()  # {stack_id → wip_id}

    for sid, wip_id in accessible.items():
        _try_add_picking(state, wip_data, job_data, out,
                      wip_id=wip_id, src_stack=sid)

    # 버퍼는 LIFO이므로 최상단 WIP만 직접 PICKING 가능
    top_buf = state.top_buffer_wip()
    if top_buf is not None and top_buf not in state.K_mach:
        _try_add_picking(state, wip_data, job_data, out,
                      wip_id=top_buf, src_stack=None)


def _try_add_picking(
    state: State,
    wip_data: Dict[int, WIPData],
    job_data:  Dict[int, JobData],
    out:       List[Action],
    wip_id: int,
    src_stack,
) -> None:
    """단일 WIP에 대해 PICKING 가능한 job 후보를 검색하여 out에 추가한다.

    Phase 10 co-loading 모드:
      - LOADING 중 j_mach 외 다른 job의 WIP도 투입 허용
      - 용량 체크는 primary job(j_mach)의 cap 기준
      - same_spec 체크는 co-load WIP끼리는 적용 안 함
    """
    wip = wip_data.get(wip_id)
    if wip is None:
        return
    if wip_id in state.K_mach:
        return
    # 출력재(is_output_wip=True)는 후속 런의 입력으로 재사용되지 않는다
    if wip.is_output_wip:
        return

    for job_id, job in job_data.items():
        if job_id not in state.Q_rem:
            continue

        # ── LOADING 상태 분기 ──────────────────────────────────────────────────
        if state.phase == MachinePhase.LOADING:
            is_secondary = _co_loading_mode and job_id != state.j_mach

            if is_secondary:
                # Co-loading 보조 job: grade+두께 + unique input_wip_id 체크만 수행
                if not _matches_job_template(wip, job):
                    continue
                if job.input_wip_id > 0 and job.input_wip_id != wip_id:
                    continue
                # 이미 이 job의 WIP이 K_mach에 있으면 추가 투입 불필요
                if any(wip_data.get(kw) is not None
                       and wip_data[kw].grade == job.grade
                       for kw in state.K_mach):
                    continue
                # 용량 체크: primary job(j_mach)의 물리적 machine cap 기준
                primary_job = job_data.get(state.j_mach) if state.j_mach is not None else None
                cap_short = primary_job.cap_short if primary_job else job.cap_short
                cap_long  = primary_job.cap_long  if primary_job else job.cap_long
            else:
                # 기존 로직: compat 체크 + j_mach 일치
                if not _compat_p4(wip_id, job_id, wip, job, state, wip_data):
                    continue
                if job_id != state.j_mach:
                    continue
                cap_short = job.cap_short
                cap_long  = job.cap_long

        else:
            # EMPTY 상태: compat 체크 (원래 로직)
            if not _compat_p4(wip_id, job_id, wip, job, state, wip_data):
                continue
            cap_short = job.cap_short
            cap_long  = job.cap_long

        # capa 체크
        new_u_short = state.u_short + wip.short_side
        new_u_long  = max(state.u_long, wip.long_side)
        if new_u_short > cap_short:
            continue
        if new_u_long > cap_long:
            continue

        out.append(Action(
            crane=CraneAction(
                type=CRANE_PICKING,
                wip_id=wip_id,
                src_stack=src_stack,
                job_id=job_id,
            ),
            prod=ProdAction(PROD_NONE),
        ))


def _compat_p4(
    wip_id: int,
    job_id: int,
    wip:    WIPData,
    job:    JobData,
    state:  State,
    wip_data: Dict[int, WIPData],
) -> bool:
    """
    EMPTY 상태 (첫 번째 PICKING):
      - job.input_wip_id > 0 이면 해당 unique WIP만 허용
      - job.input_wip_id == 0 이면 job template과 맞는 어떤 WIP든 허용

    LOADING 상태 (추가 PICKING):
      - job 일치 (j_mach == job_id) 는 호출 측에서 보장
      - 기존 K_mach 내 WIP과 same_spec이어야 함
      - 동시에 job template과도 맞아야 함
    """
    if state.phase == MachinePhase.EMPTY:
        # 원자재 job (input_wip_id==0)은 DIRECT_START만 허용, PICKING 불가
        if job.input_wip_id == 0:
            return False
        # unique job: 정확히 해당 WIP ID만 허용
        return job.input_wip_id == wip_id

    # LOADING: 추가 PICKING — K_mach 내 모든 WIP과 same_spec 체크
    if state.phase == MachinePhase.LOADING:
        if not _matches_job_template(wip, job):
            return False
        for existing_wid in state.K_mach:
            existing_wip = wip_data.get(existing_wid)
            if existing_wip is None:
                continue
            if not _same_spec(wip, existing_wip):
                return False
        return True

    return False


def _same_spec(wip1: WIPData, wip2: WIPData) -> bool:
    """
    두 WIP의 규격 동일 여부 (기준)
    동일 grade + 두께(±0.1mm) 만 체크.
    가로/세로(단변·장변)는 설비 Capa 체크(_try_add_picking의 cap_short/cap_long)로 처리.
    """
    if wip1.grade != wip2.grade:
        return False
    if abs(wip1.thickness - wip2.thickness) > 0.1:
        return False
    return True


def _matches_job_template(wip: WIPData, job: JobData) -> bool:
    """
    WIP이 job이 요구하는 규격/재질 template과 일치하는지 검사한다.
    Phase 5: grade + 두께만 체크. 가로/세로는 설비 Capa 체크로 위임.
    """
    if wip.grade != job.grade:
        return False
    if abs(wip.thickness - job.thickness) > 0.1:
        return False
    return True


# 내부: START_PROCESS 후보 생성

def _add_start_process_actions(
    state:    State,
    job_data: Dict[int, JobData],
    out:      List[Action],
) -> None:
    """
    START_PROCESS(q) 후보를 out에 추가한다.
    조건 :
      - m_t = LOADING
      - K_mach ≠ ∅
      - q = j_mach
    """
    if (state.phase == MachinePhase.LOADING
            and len(state.K_mach) > 0
            and state.j_mach is not None):
        q = state.j_mach
        out.append(Action(
            crane=CraneAction(CRANE_WAIT),
            prod=ProdAction(PROD_START, job_id=q),
        ))


# 내부: STORE 후보 생성

def _add_store_actions(
    state: State,
    out:   List[Action],
) -> None:
    """
    STORE(k, dst_stack, job_id) 후보를 out에 추가한다.
    조건:
      - m_t = BLOCKED
      - k ∈ O_wait
    """
    if state.phase != MachinePhase.BLOCKED:
        return

    available_stacks = sorted(
        state.stacks.keys(),
        key=lambda sid: len(state.stacks[sid]),
    )

    for k in state.O_wait:
        dst = available_stacks[0]
        out.append(Action(
            crane=CraneAction(
                type=CRANE_STORE,
                wip_id=k,
                dst_stack=dst,
                job_id=state.j_mach,
            ),
            prod=ProdAction(PROD_NONE),
        ))


# 내부: DIRECT_START 후보 생성 (원자재 job 전용)

def _add_direct_start_actions(
    state:    State,
    job_data: Dict[int, JobData],
    out:      List[Action],
    allow_direct_start: bool = True,
) -> None:
    """
    EMPTY 상태에서 야드에 PICKING 가능한 WIP이 없을 때,
    has_external_input=True인 원자재 run에 대해 DIRECT_START 후보를 추가한다.

    DIRECT_START: crane=WAIT (이동 없음), prod=DIRECT_START(job_id)
    전이 효과: EMPTY → BUSY (K_mach=∅, u_short=cap_short, u_long=cap_long)
    """
    if state.phase != MachinePhase.EMPTY:
        return
    if not allow_direct_start:
        return
    has_picking_candidate = any(a.crane.type == CRANE_PICKING for a in out)
    if has_picking_candidate:
        return
    for job_id, job in job_data.items():
        if job_id not in state.Q_rem:
            continue
        if not job.has_external_input:
            continue
        out.append(Action(
            crane=CraneAction(CRANE_WAIT),
            prod=ProdAction(PROD_DIRECT_START, job_id=job_id),
        ))
