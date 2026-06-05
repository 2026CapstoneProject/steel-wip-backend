"""Rolling-horizon policy backed by DIDPPy."""

from collections import Counter
from typing import Dict, Optional, Tuple

from ..data.loader import WIPData, JobData
from ..data.params import DEFAULT_HORIZON, DEFAULT_TIME_LIM, STACK_TO_NODE
from ..env.state import State, MachinePhase
from ..env.actions import (
    Action,
    CRANE_MOVE,
    CRANE_PRE_POSITION,
    CRANE_RESTORE,
    CRANE_TEMP_MOVE,
    CRANE_WAIT,
    PROD_DIRECT_START,
    PROD_START,
)
from ..env.feasibility import get_feasible_actions
from ..didp.model_builder import (
    build_didp_model, extract_first_action, compute_relevant_wip_ids,
    DIDP_AVAILABLE,
)
from ..didp.solver import solve, is_available
from .greedy import _slot_quality_bonus, greedy_policy, _time_to_next_unmanned


class StrictCAASDyError(RuntimeError):
    """Raised when strict CAASDy mode cannot produce a valid next action."""


_POLICY_STATS = Counter()


def reset_policy_stats() -> None:
    _POLICY_STATS.clear()


def get_policy_stats() -> dict:
    return dict(_POLICY_STATS)


def _bump_stat(key: str) -> None:
    _POLICY_STATS[key] += 1


def _semantic_match(action: Action, candidate: Action) -> bool:
    if action.crane.type != candidate.crane.type or action.prod.type != candidate.prod.type:
        return False

    if action.crane.type == "PICKING":
        return (
            action.crane.wip_id == candidate.crane.wip_id
            and action.crane.src_stack == candidate.crane.src_stack
            and action.crane.job_id == candidate.crane.job_id
        )
    if action.crane.type in ("MOVE", "TEMP_MOVE", "RESTORE", "PRE_POSITION"):
        return (
            action.crane.wip_id == candidate.crane.wip_id
            and action.crane.src_stack == candidate.crane.src_stack
            and action.crane.dst_stack == candidate.crane.dst_stack
        )
    if action.crane.type == "STORE":
        return True
    if action.prod.type in (PROD_DIRECT_START, PROD_START):
        return action.prod.job_id == candidate.prod.job_id
    return action == candidate


def _find_semantic_equivalent(action: Action, feasible: list[Action]) -> Optional[Action]:
    for cand in feasible:
        if _semantic_match(action, cand):
            return cand
    return None


def rolling_horizon_policy(
    state:         State,
    wip_data:      Dict[int, WIPData],
    job_data:      Dict[int, JobData],
    machine_times: Dict[str, float],
    horizon:       int   = DEFAULT_HORIZON,
    time_limit:    float = DEFAULT_TIME_LIM,
    solver_name:   str   = "CABS",
    beam_size:     int   = 1000,
    verbose:       bool  = False,
    model_cfg      = None,   # CAASDyModelConfig | None
    solver_params  = None,   # solver별 추가 파라미터 dict | None
    allow_greedy_fallback: bool = True,
) -> Tuple[Action, float]:
    """Return the next action and the estimated lookahead cost."""
    _bump_stat("calls_total")
    if not is_available() or len(state.Q_rem) == 0:
        _bump_stat("didp_unavailable_or_empty")
        if not allow_greedy_fallback:
            raise StrictCAASDyError("DIDPPy unavailable or no remaining jobs in strict CAASDy mode")
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_didp_unavailable_or_empty")
        return action, float("inf")

    feasible = get_feasible_actions(state, wip_data, job_data)
    if not feasible:
        _bump_stat("no_feasible_actions")
        if not allow_greedy_fallback:
            raise StrictCAASDyError("No feasible actions in strict CAASDy mode")
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_no_feasible_actions")
        return action, float("inf")

    relocation_like = sum(
        1
        for a in feasible
        if a.crane.type in (CRANE_MOVE, CRANE_TEMP_MOVE, CRANE_RESTORE, CRANE_PRE_POSITION)
    )
    if (
        allow_greedy_fallback
        and
        state.phase in (MachinePhase.EMPTY, MachinePhase.BUSY)
        and (
            len(feasible) >= 40
            or relocation_like >= 28
            or (len(state.Q_rem) >= 8 and relocation_like >= 20)
            )
        ):
        _bump_stat("heavy_feasible_set_detected")
        if verbose:
            print(
                "  [RH] heavy feasible-set detected "
                f"(feasible={len(feasible)}, reloc_like={relocation_like}) "
                "→ greedy fallback"
            )
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_heavy_feasible_set")
        return _refine_slot_variant(action, state, wip_data, job_data, feasible), float("inf")

    local_horizon = horizon
    local_time_limit = time_limit
    local_beam_size = beam_size
    if len(feasible) >= 24 or relocation_like >= 16:
        local_horizon = min(local_horizon, 8)
        local_time_limit = min(local_time_limit, 0.35)
        local_beam_size = min(local_beam_size, 400)
    elif len(feasible) >= 16 or relocation_like >= 10:
        local_horizon = min(local_horizon, 8)
        local_time_limit = min(local_time_limit, 0.60)
        local_beam_size = min(local_beam_size, 600)

    active_job_ids = sorted(state.Q_rem)
    active_wip_ids = compute_relevant_wip_ids(state, job_data, active_job_ids)

    model = build_didp_model(
        state, wip_data, job_data, machine_times, local_horizon,
        model_cfg=model_cfg,
    )

    if model is None:
        _bump_stat("didp_model_none")
        if not allow_greedy_fallback:
            raise StrictCAASDyError("DIDPPy model build failed in strict CAASDy mode")
        if verbose:
            print("  [RH] DIDPPy 모델 빌드 실패 → greedy fallback")
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_model_none")
        return action, float("inf")

    result = solve(model, time_limit=local_time_limit, solver=solver_name,
                   beam_size=local_beam_size, solver_params=solver_params)
    _bump_stat("didp_solve_attempts")

    if not result.success or not result.transitions:
        _bump_stat("didp_solve_failed")
        if not allow_greedy_fallback:
            raise StrictCAASDyError("DIDPPy solve failed in strict CAASDy mode")
        if verbose:
            print("  [RH] 풀이 실패 → greedy fallback")
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_solve_failed")
        return action, float("inf")
    _bump_stat("didp_solve_succeeded")

    active_job_ids_list  = sorted(state.Q_rem)
    active_wip_ids_list  = active_wip_ids

    action = extract_first_action(
        result.transitions,
        state, wip_data, job_data,
        active_wip_ids_list, active_job_ids_list,
    )

    if action is None:
        _bump_stat("didp_parse_failed")
        if not allow_greedy_fallback:
            raise StrictCAASDyError("Failed to parse first action from DIDPPy transitions")
        if verbose:
            print("  [RH] 전이 파싱 실패 → greedy fallback")
        action = greedy_policy(state, wip_data, job_data)
        _bump_stat("greedy_fallback_total")
        _bump_stat("greedy_fallback_parse_failed")
        return action, result.cost

    if action not in feasible:
        matched = _find_semantic_equivalent(action, feasible)
        if matched is not None:
            action = matched
            _bump_stat("semantic_action_match")
        elif not allow_greedy_fallback:
            raise StrictCAASDyError(f"DIDPPy produced infeasible action: {action}")
        else:
            _bump_stat("didp_infeasible_action")
            if verbose:
                print(f"  [RH] 비실행 가능 행동 감지({action}) → greedy fallback")
            action = greedy_policy(state, wip_data, job_data)
            _bump_stat("greedy_fallback_total")
            _bump_stat("greedy_fallback_infeasible_action")
            return _refine_slot_variant(action, state, wip_data, job_data, feasible), result.cost

    def _is_productive(a: Action) -> bool:
        return (
            a.crane.type != CRANE_WAIT
            or a.prod.type in (PROD_DIRECT_START, PROD_START)
        )

    if allow_greedy_fallback and action.crane.type == CRANE_WAIT and not _is_productive(action):
        _bump_stat("didp_nonproductive_wait")
        if state.phase == MachinePhase.EMPTY:
            greedy_action = greedy_policy(state, wip_data, job_data)
            if _is_productive(greedy_action):
                _bump_stat("greedy_override_total")
                _bump_stat("greedy_override_empty_wait")
                if verbose:
                    print(f"  [RH] EMPTY WAIT → greedy 우선 행동 사용 → {greedy_action}")
                return _refine_slot_variant(greedy_action, state, wip_data, job_data, feasible), result.cost
            ds_actions = [a for a in feasible if a.prod.type == PROD_DIRECT_START]
            if ds_actions:
                def _ds_score(a: Action) -> float:
                    job = job_data.get(a.prod.job_id)
                    return job.process_time if job else float("-inf")
                best_ds = max(ds_actions, key=_ds_score)
                _bump_stat("didp_override_direct_start")
                if verbose:
                    print(f"  [RH] EMPTY WAIT 대신 DIRECT_START 직접 선택 → {best_ds}")
                return _refine_slot_variant(best_ds, state, wip_data, job_data, feasible), result.cost

        greedy_action = greedy_policy(state, wip_data, job_data)
        if state.phase == MachinePhase.BUSY and greedy_action.crane.type != CRANE_WAIT:
            _bump_stat("greedy_override_total")
            _bump_stat("greedy_override_busy_wait")
            if verbose:
                print(f"  [RH] WAIT 대신 진행성 있는 greedy 행동 사용 → {greedy_action}")
            return _refine_slot_variant(greedy_action, state, wip_data, job_data, feasible), result.cost
        if state.phase == MachinePhase.EMPTY and _is_productive(greedy_action):
            _bump_stat("greedy_override_total")
            _bump_stat("greedy_override_empty_wait_alt")
            if verbose:
                print(f"  [RH] EMPTY WAIT 대신 greedy 행동 사용 → {greedy_action}")
            return _refine_slot_variant(greedy_action, state, wip_data, job_data, feasible), result.cost

    if allow_greedy_fallback and action.crane.type == CRANE_RESTORE:
        needed_wip_ids = {
            job_data[j].input_wip_id
            for j in state.Q_rem
            if job_data.get(j) and job_data[j].input_wip_id > 0
        }
        dst = getattr(action.crane, 'dst_stack', None)
        if dst is not None and needed_wip_ids:
            if any(wid in needed_wip_ids for wid in state.stacks.get(dst, [])):
                _bump_stat("greedy_override_total")
                _bump_stat("greedy_override_restore_blocked")
                if verbose:
                    print(f"  [RH] RESTORE({action.crane.wip_id}→Stack{dst}) 차단 "
                          f"— needed WIP 포함 스택. greedy 재선택")
                action = greedy_policy(state, wip_data, job_data)

    if (
        allow_greedy_fallback
        and state.phase == MachinePhase.LOADING
        and len(state.buffer_wips) > 0
        and action.prod.type == PROD_START
    ):
        greedy_action = greedy_policy(state, wip_data, job_data)
        if greedy_action.crane.type == CRANE_RESTORE:
            _bump_stat("greedy_override_total")
            _bump_stat("greedy_override_loading_restore_first")
            if verbose:
                print(
                    f"  [RH] LOADING 상태 버퍼 잔류 감지 "
                    f"— START 대신 즉시 RESTORE 선택 → {greedy_action}"
                )
            action = greedy_action

    # ── Phase16 SWP dual-margin override ───────────────────────────────────────
    # DIDP(beam search)는 H=10 horizon 내 국소 최적을 추구하므로,
    # 야간무인 전 창(shift window)에서 generates_output job을 먼저 선택하면
    # 이후 사이클(manned3)에 더 많은 잔여 job이 남는 구조적 손실을 놓칠 수 있음.
    #
    # 조건: EMPTY 상태 + DIDP가 generates_output job 선택
    #       + greedy SWP dual-margin이 no-output job을 더 좋은 pack으로 선택
    #       + 유의미한 shift window 존재 (t_to_unm > 60분)
    # → greedy SWP 결과로 교체.
    #
    # 예) buffer_stress Plan2 t=321.78: DIDP→Job9(gen_out=True),
    #     greedy→Job803-계열(gen_out=False), pack 214.58 > 211.015 → override
    if (
        allow_greedy_fallback
        and
        state.phase == MachinePhase.EMPTY
        and action.prod.type == PROD_DIRECT_START
        and job_data.get(action.prod.job_id)
        and job_data[action.prod.job_id].generates_output
    ):
        t_to_unm = _time_to_next_unmanned(state.clock, state.shift_cfg)
        if t_to_unm > 60.0:
            swp_greedy = greedy_policy(state, wip_data, job_data)
            if (
                swp_greedy is not None
                and swp_greedy.prod.type == PROD_DIRECT_START
                and job_data.get(swp_greedy.prod.job_id)
                and not job_data[swp_greedy.prod.job_id].generates_output
            ):
                _bump_stat("greedy_override_total")
                _bump_stat("greedy_override_swp_dual_margin")
                if verbose:
                    print(
                        f"  [RH] SWP dual-margin override: "
                        f"DIDP→Job{action.prod.job_id}(gen_out) "
                        f"→ greedy→Job{swp_greedy.prod.job_id}(no_out)"
                    )
                action = swp_greedy

    if verbose:
        print(f"  [RH] {result.solver_name} cost={result.cost:.2f} "
              f"→ {action}")

    _bump_stat("didp_action_accepted")
    return _refine_slot_variant(action, state, wip_data, job_data, feasible), result.cost


def _refine_slot_variant(
    action: Action,
    state: State,
    wip_data: Dict[int, WIPData],
    job_data: Dict[int, JobData],
    feasible: Optional[list] = None,
) -> Action:
    """
    Phase12용 후처리:
    DIDP는 slot quality를 직접 모르므로,
    동일한 (wip, src, job) PICKING의 여러 slot variant 중 더 좋은 것을 고른다.
    """
    if action.crane.type != "PICKING":
        return action
    if feasible is None:
        feasible = get_feasible_actions(state, wip_data, job_data)

    same_pick_variants = [
        a for a in feasible
        if a.crane.type == action.crane.type
        and a.crane.wip_id == action.crane.wip_id
        and a.crane.src_stack == action.crane.src_stack
        and a.crane.job_id == action.crane.job_id
    ]
    if not same_pick_variants:
        return action

    return max(same_pick_variants, key=lambda a: _slot_quality_bonus(state, a, wip_data))
