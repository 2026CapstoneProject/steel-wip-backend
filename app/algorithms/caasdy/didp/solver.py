"""
DIDPPy 솔버 래퍼


CABS (Cost-Algebraic Beam Search): Anytime 알고리즘, 시간 제한 내 최선 해
CAASDy: 빠른 근사 해, 실시간 응답이 필요할 때

DIDPPy 미설치 시 graceful fallback 제공.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import os
import sys
from typing import Any, Dict, List, Optional

try:
    import didppy as dp
    DIDP_AVAILABLE = True
except ImportError:
    DIDP_AVAILABLE = False
    dp = None


@dataclass
class SolverResult:
    """DIDPPy 풀이 결과"""
    transitions: List[Any]   # dp.Transition 객체 리스트 (첫 행동만 사용)
    cost:        float
    solver_name: str
    success:     bool = True

    @property
    def first_transition_name(self) -> Optional[str]:
        if self.transitions:
            return self.transitions[0].name
        return None


@contextmanager
def _suppress_stderr():
    """
    DIDPPy 내부 grounding 경고가 stderr로 과도하게 출력되는 것을 억제한다.
    """
    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        yield
        return

    saved_fd = os.dup(stderr_fd)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), stderr_fd)
            yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(saved_fd)


def solve(
    model: Any,
    time_limit: float = 3.0,
    solver: str = "CABS",
    beam_size: int = 1000,
    solver_params: Optional[Dict[str, Any]] = None,
) -> SolverResult:
    """
    DIDPPy 모델을 풀어 최적 행동 시퀀스를 반환한다.

    Args:
        model:      dp.Model 객체
        time_limit: 솔버 제한 시간 (초)
        solver:     "CABS" | "CAASDy" | "DFBB" | "DBDFS"
        beam_size:  CABS initial_beam_size (DIDPPy 0.10+ 파라미터)
        solver_params: solver별 추가 키워드 인자

    Returns:
        SolverResult
    """
    if not DIDP_AVAILABLE or model is None:
        return SolverResult(transitions=[], cost=float("inf"),
                            solver_name="NONE", success=False)

    try:
        with _suppress_stderr():
            s = _build_solver_instance(
                model=model,
                solver=solver,
                time_limit=time_limit,
                beam_size=beam_size,
                solver_params=solver_params or {},
            )

            # DIDPPy 0.10+: search()가 Solution 객체 반환 (튜플 언팩 X)
            sol = s.search()

        if sol.cost is None or sol.is_infeasible:
            return SolverResult(transitions=[], cost=float("inf"),
                                solver_name=solver, success=False)

        return SolverResult(
            transitions=sol.transitions,
            cost=float(sol.cost),
            solver_name=solver,
            success=True,
        )

    except Exception as e:
        print(f"[DIDPPy solver error] {e}")
        return SolverResult(transitions=[], cost=float("inf"),
                            solver_name=solver, success=False)


def is_available() -> bool:
    """DIDPPy 사용 가능 여부"""
    return DIDP_AVAILABLE


_SOLVER_CLASS_NAMES = {
    "CABS": "CABS",
    "CAASDy": "CAASDy",
    "DFBB": "DFBB",
    "DBDFS": "DBDFS",
    "LNBS": "LNBS",
    "ACPS": "ACPS",
    "APPS": "APPS",
    "CBFS": "CBFS",
    "BreadthFirstSearch": "BreadthFirstSearch",
    "DDLNS": "DDLNS",
    "WeightedAstar": "WeightedAstar",
}


def _filter_kwargs(callable_obj, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """실제 시그니처가 받는 키만 남긴다."""
    sig = inspect.signature(callable_obj)
    allowed = set(sig.parameters.keys())
    return {k: v for k, v in kwargs.items() if k in allowed and v is not None}


def _build_solver_instance(
    model: Any,
    solver: str,
    time_limit: float,
    beam_size: int,
    solver_params: Dict[str, Any],
):
    """
    DIDPPy solver 객체 생성.

    문서/버전 차이로 solver별 인자 구성이 조금씩 다를 수 있어,
    실제 생성자 시그니처를 보고 허용되는 인자만 전달한다.
    """
    solver_name = solver if solver in _SOLVER_CLASS_NAMES else "CABS"
    cls_name = _SOLVER_CLASS_NAMES[solver_name]
    cls = getattr(dp, cls_name)

    base_kwargs: Dict[str, Any] = {
        "model": model,
        "time_limit": time_limit,
        "quiet": True,
    }

    extra_kwargs: Dict[str, Any] = dict(solver_params)

    if solver_name == "CABS":
        extra_kwargs.setdefault("initial_beam_size", beam_size)
    elif solver_name == "LNBS":
        extra_kwargs.setdefault("initial_beam_size", beam_size)
    elif solver_name == "DDLNS":
        extra_kwargs.setdefault("beam_size", beam_size)

    kwargs = _filter_kwargs(cls, {**base_kwargs, **extra_kwargs})
    return cls(**kwargs)
