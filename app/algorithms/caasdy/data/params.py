"""파라미터 정의"""

from dataclasses import dataclass
from typing import Optional

# ── 교대 사이클 기본값 ─────────────────────────────────────────────────────────
# 4단계: 오전 유인 → 점심 무인 → 오후 유인 → 야간 무인
MANNED1_DURATION = 180.0   # 오전 유인가공 (3시간)
UNM1_DURATION    =  60.0   # 점심 무인 (1시간)
MANNED2_DURATION = 300.0   # 오후 유인가공 (5시간)
UNM2_DURATION    = 720.0   # 야간 무인 (퇴근, 12시간)

# 하위 호환 aliases
MANNED_DURATION  = MANNED1_DURATION
UNM_DURATION     = UNM1_DURATION
SHIFT_END        = MANNED1_DURATION
UNM_END          = MANNED1_DURATION + UNM1_DURATION


@dataclass
class ShiftConfig:
    """
    교대 사이클 파라미터 — 4단계 유인/무인 패턴을 런타임에 설정 가능.

    하루 패턴 (기본값):
      오전 유인 180분 → 점심 무인 60분 → 오후 유인 300분 → 야간 무인 720분
      총 사이클: 1260분 (21시간)

    Parameters
    ----------
    manned1_minutes : 오전 유인가공 시간 (분). 크레인이 작동하는 1번째 구간.
    unm1_minutes    : 점심 무인 시간 (분). 크레인이 정지하는 1번째 구간.
    manned2_minutes : 오후 유인가공 시간 (분). 크레인이 작동하는 2번째 구간.
    unm2_minutes    : 야간 무인 시간 (분). 크레인이 정지하는 2번째 구간 (퇴근).
    """
    manned1_minutes: float = MANNED1_DURATION
    unm1_minutes:    float = UNM1_DURATION
    manned2_minutes: float = MANNED2_DURATION
    unm2_minutes:    float = UNM2_DURATION

    # ── 하위 호환 속성 ─────────────────────────────────────────────────────────
    @property
    def manned_minutes(self) -> float:
        """[호환] 첫 번째 유인가공 시간"""
        return self.manned1_minutes

    @property
    def unm_minutes(self) -> float:
        """[호환] 첫 번째 무인가공 시간"""
        return self.unm1_minutes

    @property
    def shift_end(self) -> float:
        """[호환] 첫 번째 유인 종료 시점"""
        return self.manned1_minutes

    # ── 핵심 속성 ──────────────────────────────────────────────────────────────
    @property
    def cycle_minutes(self) -> float:
        """교대 사이클 전체 길이 (분)"""
        return self.manned1_minutes + self.unm1_minutes + self.manned2_minutes + self.unm2_minutes

    # ── 무인 구간 경계 (사이클 내 절대 시각) ──────────────────────────────────
    @property
    def _unm1_start(self) -> float:
        return self.manned1_minutes

    @property
    def _unm1_end(self) -> float:
        return self.manned1_minutes + self.unm1_minutes

    @property
    def _unm2_start(self) -> float:
        return self.manned1_minutes + self.unm1_minutes + self.manned2_minutes

    @property
    def _unm2_end(self) -> float:
        return self.cycle_minutes

    # ── 핵심 메서드 ────────────────────────────────────────────────────────────
    def is_unm_at(self, t_rel: float) -> bool:
        """
        사이클 내 상대 시각 t_rel에서 무인가공(크레인 정지) 구간 여부.
        t_rel은 이미 clock % cycle 로 계산된 값이어야 한다.
        """
        return (self._unm1_start <= t_rel < self._unm1_end or
                t_rel >= self._unm2_start)

    def rem_manned_at(self, t_rel: float) -> float:
        """
        t_rel 시점에서 다음 무인 구간 시작까지 남은 유인가공 시간 (분).
        이미 무인 구간이면 0.0 반환.
        """
        if t_rel < self._unm1_start:
            return self._unm1_start - t_rel   # 점심까지 잔여 오전 유인
        if t_rel < self._unm1_end:
            return 0.0                         # 점심 중
        if t_rel < self._unm2_start:
            return self._unm2_start - t_rel   # 야간까지 잔여 오후 유인
        return 0.0                             # 야간 중

    def unm_overlap(self, t_rel: float, tau: float) -> float:
        """
        t_rel 부터 tau분 동안 무인 구간과 겹치는 시간(분) 계산.
        멀티 사이클 방지를 위해 tau가 사이클을 초과하면 사이클 내로 clamp.
        """
        t_end = min(t_rel + tau, self.cycle_minutes)
        a1, b1 = self._unm1_start, self._unm1_end
        a2, b2 = self._unm2_start, self._unm2_end
        return (max(0.0, min(t_end, b1) - max(t_rel, a1)) +
                max(0.0, min(t_end, b2) - max(t_rel, a2)))


# 기본 교대 설정 (런타임 변경 전 import 시 사용할 singleton)
DEFAULT_SHIFT: ShiftConfig = ShiftConfig()


DELTA_MIN = 1.0          # 최소 시간 단위 (분). τ(WAIT) = DELTA_MIN > 0

DEFAULT_CAP_SHORT = 2500.0   # mm
DEFAULT_CAP_LONG  = 7000.0   # mm

BUFFER_CAP = 2           # 크레인 임시 버퍼 슬롯 수 (BUF-1, 동시 2개 허용)


@dataclass
class MachineCapConfig:
    """
    설비(기계) 용량 파라미터.

    load_plan / load_exp_plan 에 전달하면 job_data의 cap_short / cap_long 이
    아래 규칙으로 최종 결정된다.

      cap_short_final = cap_short  (지정 시) | batch * spec_short  (None 시 CSV 기반)
      cap_long_final  = cap_long   (지정 시) | spec_long           (None 시 CSV 기반)

    auto_expand_for_carried=True(기본값) 이면, 이전 플랜에서 반입된 carried WIP의
    치수가 결정된 cap을 초과할 때 자동으로 cap을 WIP 치수에 맞게 확장한다.
    → Plan-to-plan 이월 시 치수 불일치로 발생하는 데드락을 방지.

    Examples
    --------
    MachineCapConfig()                          # 기본값: CSV 그대로 + 자동 확장
    MachineCapConfig(cap_long=3000.0)           # 장변 용량을 3000mm로 고정
    MachineCapConfig(cap_short=2000.0,
                     cap_long=4000.0)           # 단·장변 모두 고정
    MachineCapConfig(auto_expand_for_carried=False)  # 자동 확장 비활성
    """
    cap_short:               Optional[float] = None  # 설비 단변 용량 (mm). None=CSV 기반
    cap_long:                Optional[float] = None  # 설비 장변 용량 (mm). None=CSV 기반
    auto_expand_for_carried: bool            = True  # carried WIP 치수 초과 시 자동 확장

C_REL  = 5.0             # 영구 재배치 페널티  (c^rel)
C_TEMP = 2.0             # 임시 이동 페널티    (c^temp)
R_FILL = 10.0            # 적재율 보상 승수    (r^fill)
R_UNM  = 0.05            # 무인가공 보상/분    (r^unm)

W_SHORT = 0.5            # short-side 가중치 (ω^short)
W_LONG  = 0.5            # long-side  가중치 (ω^long)

P_RUN    = 100.0         # 미완료 run당 패널티
P_BUFFER = 5.0           # 버퍼 미복원 WIP당 패널티
P_MACH   = 20.0          # 설비 위 미시작 WIP당 패널티
P_BLOCKER = 10.0         # 필요 WIP 위에 눌린 blocker WIP당 terminal 패널티
C_IDLE_WAIT = 0.1        # RH lookahead에서 유휴 WAIT procrastination 방지용 미세 패널티

C_PRE_BONUS = 0.5        # PRE_POSITION DyPDL 보상 (RESTORE 대비 우선 선택 유도)
SIGMA_PTIME = 0.0        # 생산시간 표준편차 (0.0=결정론적, >0=확률적 노이즈)


@dataclass
class CAASDyModelConfig:
    """
    CAASDy (DyPDL) 목적함수 가중치 파라미터.

    이 클래스의 값은 DyPDL 모델 내부 전이 비용·보상에만 적용된다.
    실제 시뮬레이션의 step_cost()는 params.py 전역 상수를 그대로 사용하므로,
    모델 파라미터를 바꿔도 실제 비용 계산은 변하지 않는다.
    (lookahead 목적함수 형태만 달라짐 → 의사결정 전략이 달라짐)

    Parameters
    ----------
    c_rel       : 영구 재배치(MOVE) 전이 비용.  기본 5.0
    c_temp      : 임시 이동(TEMP_MOVE) 전이 비용. 기본 2.0
    r_fill      : 적재율 보상 승수.              기본 10.0
    w_short     : short-side 적재율 가중치.      기본 0.5
    w_long      : long-side  적재율 가중치.      기본 0.5
    p_run       : Horizon 끝 미완료 run당 패널티. 기본 100.0
    p_buffer    : Horizon 끝 버퍼 WIP당 패널티.  기본 5.0
    c_idle_wait : 유휴 WAIT 미세 패널티.         기본 0.1
    c_pre_bonus : PRE_POSITION 보상.             기본 0.5
    """
    c_rel:       float = C_REL
    c_temp:      float = C_TEMP
    r_fill:      float = R_FILL
    w_short:     float = W_SHORT
    w_long:      float = W_LONG
    p_run:       float = P_RUN
    p_buffer:    float = P_BUFFER
    c_idle_wait: float = C_IDLE_WAIT
    c_pre_bonus: float = C_PRE_BONUS

    def label(self) -> str:
        """기본값과 다른 파라미터만 표기하는 짧은 레이블 생성."""
        defaults = CAASDyModelConfig()
        parts = []
        for fname, fval in [
            ("cR", self.c_rel),    ("cT", self.c_temp),
            ("rF", self.r_fill),   ("wS", self.w_short),  ("wL", self.w_long),
            ("pR", self.p_run),    ("pB", self.p_buffer),
            ("cI", self.c_idle_wait), ("cP", self.c_pre_bonus),
        ]:
            default_val = getattr(defaults, {
                "cR": "c_rel", "cT": "c_temp", "rF": "r_fill",
                "wS": "w_short", "wL": "w_long", "pR": "p_run",
                "pB": "p_buffer", "cI": "c_idle_wait", "cP": "c_pre_bonus",
            }[fname])
            if abs(fval - default_val) > 1e-9:
                parts.append(f"{fname}={fval}")
        return " ".join(parts) if parts else "default"

# LocationId → 크레인 시간표 노드명 (서비스 DB locations 기준)
# Zone 1(A): loc_id 1~4  → A-1..A-4
# Zone 2(B): loc_id 5~10 → B-1..B-6  (보관 스택, 버퍼 아님!)
# S4 장비 슬롯: loc_id 11~14 → PICKING 목적지 (distance_matrix 노드 아님)
STACK_TO_NODE = {
    1: "A-1", 2: "A-2", 3: "A-3", 4: "A-4",           # Zone 1 (A)
    5: "B-1", 6: "B-2", 7: "B-3", 8: "B-4",           # Zone 2 (B) 전반부
    9: "B-5", 10: "B-6",                                # Zone 2 (B) 후반부
}
NODE_TO_STACK = {v: k for k, v in STACK_TO_NODE.items()}

# 크레인 임시 버퍼 슬롯 (TEMP_MOVE 목적지) — 물리 버퍼 BUF-1 단일 위치
BUFFER_NODES = ["BUF-1"]

# 거리행렬에서 버퍼 이동시간 계산에 쓸 proxy 노드
# (BUF-1은 distance_matrix에 없으므로 가장 가까운 B-6를 proxy로 사용)
BUFFER_NODE = "B-6"

# 설비 노드 이름 (Experiment_data / distance_matrix_Lazer1.csv 기준)
MACHINE_NODE = "Lazer-1"

DEFAULT_HORIZON   = 10   # 기본 lookahead 스텝 수
DEFAULT_TIME_LIM  = 3000  # 솔버 제한 시간 (초)
MAX_SIM_STEPS     = 3000  # 시뮬레이션 최대 스텝 수 (full production_plan 대응)
