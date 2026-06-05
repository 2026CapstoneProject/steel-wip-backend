"""상태 클래스 (Phase12: 2x2 mach_slots + slot feasibility 1차 구현)"""

import copy
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, FrozenSet, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..data.params import ShiftConfig


class MachinePhase(IntEnum):
    """
    설비 phase
    EMPTY   → LOADING (첫 PICKING 발생)
    LOADING → BUSY    (START_PROCESS 호출)
    BUSY    → BLOCKED (생산 완료, η_t ≤ τ)
    BLOCKED → EMPTY   (모든 출력재 STORE 완료)
    """
    EMPTY   = 0
    LOADING = 1
    BUSY    = 2
    BLOCKED = 3


def _default_shift() -> "ShiftConfig":
    """기본 ShiftConfig 반환 (field default_factory용)"""
    from ..data.params import ShiftConfig
    return ShiftConfig()


def _default_mach_slots() -> Dict[str, Optional[int]]:
    """
    Phase12 1차 구현용 기본 슬롯 상태.

    TL/TR/BL/BR = TopLeft, TopRight, BottomLeft, BottomRight

    슬롯 값 규약
    ────────────
    None       : 빈 슬롯 (설비에 자재 없음)
    wip_id > 0 : 해당 WIP이 슬롯을 점유 중
    0          : 원자재 런(DIRECT_START) 점유 — 물리 WIP 추적 없음.
                 wip_data에 존재하지 않으므로 UI/서비스에서는 "원자재 가공 중"으로 표시.

    이 함수는 transition.py에서도 import하여 사용한다.
    두 파일에 중복 정의하지 않도록 state.py가 단일 출처(SSOT)다.
    """
    return {
        "TL": None,
        "TR": None,
        "BL": None,
        "BR": None,
    }


def _default_mach_footprints() -> Dict[int, Tuple[str, ...]]:
    return {}


@dataclass
class State:
    """
    전체 상태 S_t

    stacks     : {stack_id → [wip_id_bottom … wip_id_top]}
                 level 1이 바닥, index -1이 최상단 (LIFO)
    crane_loc  : 크레인 현재 위치 노드명 (예: "A-1")

    buffer_wips: 버퍼에 있는 WIP ID 집합
    buffer_cap : 잔여 버퍼 용량

    phase      : MachinePhase
    K_mach     : 현재 설비 위 WIP ID 집합 (FrozenSet)
    j_mach     : 현재 목표 job ID (None if EMPTY)
    u_short    : Σ s_k (파생 — K_mach에서 계산)
    u_long     : max l_k (파생 — K_mach에서 계산)
    eta        : 잔여 가공시간 (분)
    O_wait     : 완료 후 미적재 출력재 ID 집합

    clock      : wall-clock 시간 (분)

    Q_rem      : 미시작 job ID 집합
    Q_done     : 완료된 job ID 집합

    shift_cfg  : 교대 사이클 파라미터 (유인/무인 가공 시간).
                 기본값 = ShiftConfig() (params.py의 MANNED_DURATION, UNM_DURATION).
                 build_initial_state()에서 주입하거나, 직접 지정 가능.
    """

    stacks:     Dict[int, List[int]]   # {stack_id → [wip_id, ...]}
    crane_loc:  str                    # 현재 크레인 위치 노드명

    buffer_wips: FrozenSet[int]
    buffer_cap:  int

    phase:    MachinePhase
    K_mach:   FrozenSet[int]
    j_mach:   Optional[int]
    u_short:  float
    u_long:   float
    eta:      float
    O_wait:   FrozenSet[int]

    clock:    float                    # 누적 시간 (분)

    Q_rem:    FrozenSet[int]
    Q_done:   FrozenSet[int]

    step:     int = 0

    # 교대 사이클 파라미터 — 기본값은 params.py의 MANNED_DURATION / UNM_DURATION
    shift_cfg: "ShiftConfig" = field(default_factory=_default_shift)

    # Phase 10: 이종 WIP 동시 투입 (co-loading) 지원
    # j_mach_set : 현재 설비에서 동시 생산 중인 추가 job ID 집합 (j_mach 제외)
    # 기본값 frozenset() → 기존 Phase7~9 코드와 완전 호환
    j_mach_set: FrozenSet[int] = field(default_factory=frozenset)

    # Phase 12: 설비 적재 슬롯 상태 (2x2)
    # 값 규약: None=빈 슬롯 / wip_id>0=해당 WIP 점유 / 0=원자재 런 점유
    # 동기화 정책:
    #   EMPTY / LOADING / BUSY(K_mach≠∅) → K_mach 기준 (short_side 큰 순서 first-fit)
    #   BUSY(K_mach=∅, DIRECT_START)      → 전 슬롯 0 (원자재 가공 중 표시)
    #   BLOCKED                            → O_wait 기준 (출력재가 물리적으로 설비 위에 있음)
    # 현재 단계에서는 feasibility에서 실제 slot packing 가능성도 함께 본다.
    mach_slots: Dict[str, Optional[int]] = field(default_factory=_default_mach_slots)
    mach_footprints: Dict[int, Tuple[str, ...]] = field(default_factory=_default_mach_footprints)

    # 편의 메서드

    def top_wip_of(self, stack_id: int) -> Optional[int]:
        """stack_id 스택의 최상단 WIP ID를 반환 (없으면 None)"""
        stk = self.stacks.get(stack_id, [])
        return stk[-1] if stk else None

    def accessible_wips(self) -> Dict[int, int]:
        """
        현재 접근 가능한 WIP: {stack_id → top_wip_id}
        (버퍼 내 WIP 제외 — Phase 1에서는 yard top만 고려)
        """
        result = {}
        for sid, stk in self.stacks.items():
            if stk:
                result[sid] = stk[-1]
        return result

    def is_unm(self) -> bool:
        """
        무인가공 시간대(크레인 정지 구간) 여부.

        4단계 사이클:
          오전 유인(manned1) → 점심 무인(unm1) → 오후 유인(manned2) → 야간 무인(unm2)

        예) manned1=180, unm1=60, manned2=300, unm2=720, cycle=1260
            clock=200  → 200 % 1260 = 200  → 점심 중 → True
            clock=300  → 300 % 1260 = 300  → 오후 유인 → False
            clock=1000 → 1000 % 1260 = 1000 → 야간 중 → True
        """
        t_rel = self.clock % self.shift_cfg.cycle_minutes
        return self.shift_cfg.is_unm_at(t_rel)

    def rem_shift(self) -> float:
        """
        현재 유인가공 구간 종료까지 남은 시간 (분).
        이미 무인가공 중이면 0.0 반환.
        """
        t_rel = self.clock % self.shift_cfg.cycle_minutes
        return self.shift_cfg.rem_manned_at(t_rel)

    def rem_unm(self) -> float:
        """
        현재 무인가공 구간 종료까지 남은 시간 (분).
        유인가공 중이면 0.0 반환.
        """
        cfg = self.shift_cfg
        t_rel = self.clock % cfg.cycle_minutes
        if t_rel < cfg._unm1_start:
            return 0.0                            # 오전 유인 중
        if t_rel < cfg._unm1_end:
            return cfg._unm1_end - t_rel          # 점심 중 → 점심 잔여
        if t_rel < cfg._unm2_start:
            return 0.0                            # 오후 유인 중
        return cfg.cycle_minutes - t_rel          # 야간 중 → 야간 잔여

    def is_terminal(self, max_steps: int = 0) -> bool:
        """에피소드 종료 조건: 모든 job 완료 + 미적재 출력재/버퍼 WIP 없음

        max_steps: 0이면 step 상한 없음 (simulator가 range(MAX_SIM_STEPS)로 제어).
                   양수이면 s.step >= max_steps 시 강제 종료.
        """
        all_done = (
            len(self.Q_rem) == 0
            and len(self.O_wait) == 0
            and len(self.buffer_wips) == 0
        )
        if max_steps > 0:
            return all_done or (self.step >= max_steps)
        return all_done

    def copy(self) -> "State":
        return copy.deepcopy(self)

    def summary(self) -> str:
        """현재 상태 간략 요약 문자열"""
        unm_str = " [무인가공]" if self.is_unm() else ""
        lines = [
            f"Step {self.step:3d} | clock={self.clock:6.1f}min | "
            f"phase={self.phase.name}{unm_str}",
            f"  Q_rem={sorted(self.Q_rem)} | Q_done={sorted(self.Q_done)}",
            f"  K_mach={sorted(self.K_mach)} q={self.j_mach} "
            + (f"co={sorted(self.j_mach_set)} " if self.j_mach_set else "")
            + f"u_s={self.u_short:.0f} u_l={self.u_long:.0f} η={self.eta:.1f}",
            "  mach_slots="
            + "{"
            + ", ".join(f"{k}:{v}" for k, v in self.mach_slots.items())
            + "}",
            "  mach_footprints="
            + "{"
            + ", ".join(f"{wid}:{fp}" for wid, fp in self.mach_footprints.items())
            + "}",
            f"  O_wait={sorted(self.O_wait)}",
        ]
        return "\n".join(lines)


# 초기 상태 생성 헬퍼

def build_initial_state(
    wip_data: dict,
    job_data:  dict,
    buffer_cap: int = 3,
    initial_crane_loc: str = "A-1",
    shift_cfg: Optional["ShiftConfig"] = None,
) -> State:
    """
    wip_data, job_data로부터 초기 State S_0 를 생성한다.

    Parameters
    ----------
    shift_cfg : 교대 사이클 파라미터 (유인/무인 가공 시간).
                None이면 params.py의 기본값(ShiftConfig())을 사용.

    Notes
    -----
    - stacks: inventory 위치 정보를 stack별 LIFO 리스트로 변환
      (level이 낮은 순서 = 바닥부터 쌓임)
    - 설비 EMPTY, 버퍼 비어있음
    """
    from ..data.loader import WIPData
    from ..data.params import STACK_TO_NODE, ShiftConfig as _ShiftConfig

    if shift_cfg is None:
        shift_cfg = _ShiftConfig()

    # stack 구성: level 오름차순 정렬 → index 0=바닥, -1=최상단
    stacks: Dict[int, List[int]] = {sid: [] for sid in STACK_TO_NODE}
    for wip in wip_data.values():
        if wip.stack_id not in stacks or wip.level <= 0:
            continue
        stacks[wip.stack_id].append((wip.level, wip.wip_id))

    for sid in stacks:
        stacks[sid].sort(key=lambda x: x[0])      # level 오름차순 정렬
        stacks[sid] = [wid for (_, wid) in stacks[sid]]  # wip_id만 남김

    return State(
        stacks      = stacks,
        crane_loc   = initial_crane_loc,
        buffer_wips = frozenset(),
        buffer_cap  = buffer_cap,
        phase       = MachinePhase.EMPTY,
        K_mach      = frozenset(),
        j_mach      = None,
        u_short     = 0.0,
        u_long      = 0.0,
        eta         = 0.0,
        O_wait      = frozenset(),
        clock       = 0.0,
        Q_rem       = frozenset(job_data.keys()),
        Q_done      = frozenset(),
        step        = 0,
        shift_cfg   = shift_cfg,
        j_mach_set  = frozenset(),
        mach_slots  = _default_mach_slots(),
        mach_footprints = _default_mach_footprints(),
    )
