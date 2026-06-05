"""
caasdy/service_interface.py
────────────────────────────────────────────────────────────────────────────
서비스(FastAPI 백엔드) ↔ CAASDy 솔버 브리지

이 모듈은 순수 Python 연산만 수행하며, DB(SQLAlchemy) 의존성이 없다.
호출 측(caasdy_adapter.py)이 DB 조회/저장을 담당하고, 이 모듈은
데이터 변환과 솔버 실행만 담당한다.

주요 공개 함수
──────────────
  build_solver_input()  : DB 레코드 → 솔버 입력(WIPData, JobData, 거리행렬)
  run_caasdy_solver()   : 솔버 실행 → action log 반환
  log_to_batch_plan()   : action log → BatchItem 생성용 dict 목록 변환

서비스 레이아웃
──────────────
  야드 스택 10개: A-1~A-4 (loc_id 1~4), B-1~B-6 (loc_id 5~10)
  S4 장비 슬롯:  S4-1~S4-4 (loc_id 11~14) — PICKING 목적지
  버퍼:          BUF-1 (단일 물리 슬롯, BUFFER_CAP=2 동시 WIP 허용)
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 솔버 내부 모듈 (상대 import) ───────────────────────────────────────────────
from .data.loader import WIPData, JobData, get_crane_time
from .data.params import (
    ShiftConfig, BUFFER_CAP, STACK_TO_NODE, NODE_TO_STACK,
    BUFFER_NODES, BUFFER_NODE, MACHINE_NODE, CAASDyModelConfig,
    DEFAULT_HORIZON, DEFAULT_TIME_LIM,
    C_REL, C_TEMP, C_RESTORE, R_FILL, W_SHORT, W_LONG,
    P_RUN, P_BUFFER, C_IDLE_WAIT, C_PRE_BONUS,
)
from .env.state import build_initial_state
from .env.actions import (
    CRANE_PICKING, CRANE_STORE, CRANE_MOVE,
    CRANE_TEMP_MOVE, CRANE_RESTORE, CRANE_PRE_POSITION, CRANE_WAIT,
    PROD_DIRECT_START,
)
from .env.feasibility import set_co_loading as _feas_set_coload
from .env.transition import (
    set_co_loading as _trans_set_coload,
    set_buffer_nearest_mode as _trans_set_buf_nearest,
)
from .simulation.simulator import run_episode
from .policy.rolling_horizon import rolling_horizon_policy

# ── Phase16 설정 상수 (서비스 전용) ───────────────────────────────────────────
SOLVER_NAME          = "CAASDy"
HORIZON              = 10
TIME_LIMIT           = 1000   # Phase16: 1000ms
BEAM_SIZE            = 1000
CO_LOADING_ENABLED   = True
BUFFER_NEAREST_MODE  = True   # Phase16: 버퍼 이동시간 = 최근접 스택 거리

# 교대 사이클 (Phase16 기준)
MANNED1_MINUTES = 180.0   # 오전 유인가공
UNM1_MINUTES    =  60.0   # 점심 무인
MANNED2_MINUTES = 300.0   # 오후 유인가공
UNM2_MINUTES    = 900.0   # 야간 무인 (Phase16: 15시간)

MAX_CLOCK = 50_000.0   # Phase16: 대형 시나리오 대응 상한

# Co-loading 활성화
_feas_set_coload(CO_LOADING_ENABLED)
_trans_set_coload(CO_LOADING_ENABLED)

# Phase16: 버퍼 최근접-스택 모드 활성화
_trans_set_buf_nearest(BUFFER_NEAREST_MODE)

# ── 서비스 BatchActionType 문자열 상수 ────────────────────────────────────────
_ACT_PICKING   = "PICKING"
_ACT_INBOUND   = "INBOUND"
_ACT_RELOCATE  = "RELOCATE"
_ACT_TEMP_MOVE = "TEMP_MOVE"
_ACT_RESTORE   = "RESTORE"
_ACT_DIRECT_START = "DIRECT_START"

# 솔버 크레인 액션 → 서비스 BatchActionType 매핑
_CRANE_TO_BATCH: Dict[str, str] = {
    CRANE_PICKING    : _ACT_PICKING,
    CRANE_STORE      : _ACT_INBOUND,
    CRANE_MOVE       : _ACT_RELOCATE,
    CRANE_TEMP_MOVE  : _ACT_TEMP_MOVE,
    CRANE_RESTORE    : _ACT_RESTORE,
    CRANE_PRE_POSITION: _ACT_RESTORE,  # 버퍼에서 나오면 항상 원상복구로 해석
}

# ── 공개 상수 재노출 (caasdy_adapter.py 참조용) ───────────────────────────────
SOLVER_STACK_TO_NODE: Dict[int, str] = STACK_TO_NODE
SOLVER_NODE_TO_STACK: Dict[str, int] = NODE_TO_STACK
SOLVER_BUFFER_NODES:  List[str]      = BUFFER_NODES
SOLVER_MACHINE_NODE:  str            = MACHINE_NODE


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DB 레코드 → 솔버 입력 변환
# ═══════════════════════════════════════════════════════════════════════════════

def build_solver_input(
    wip_records:     List[dict],
    cutting_records: List[dict],
    data_dir:        str,
) -> Tuple[Dict[int, WIPData], Dict[int, JobData], Dict, Dict]:
    """
    DB 레코드를 솔버 입력 형식으로 변환한다.

    Parameters
    ----------
    wip_records : SteelWip DB 레코드 목록.
        각 dict 필드:
          id           (int)   — DB PK (솔버 wip_id로 사용)
          loc_name     (str)   — 위치 노드명 (예: "A-1"). None=야드 외부.
          stack_level  (int)   — 적재 층 (1=바닥). 0=위치 없음.
          is_target    (bool)  — True=이번 작업지시서에서 피킹 대상
          material     (str)   — 재질(grade)
          thickness    (float) — 두께 (mm)
          width        (float) — 폭 (mm)
          length       (float) — 길이 (mm)

    cutting_records : LazerCutting DB 레코드 목록.
        각 dict 필드:
          id                     (int)       — DB PK (솔버 job_id로 사용)
          steel_wip_id           (int|None)  — 입력 WIP ID. None=원자재.
          estimated_cutting_time (int)       — 가공 예상 시간 (분)
          input_material         (str|None)  — 재질
          input_width            (float|None)— 입력 WIP 폭 (mm)
          input_length           (float|None)— 입력 WIP 길이 (mm)
          has_output             (bool)      — 출력 WIP 발생 여부
          output_placeholder_id  (int|None)  — 출력 WIP 임시 ID

    data_dir : distance_matrix_Lazer1.csv 가 있는 폴더 경로.

    Returns
    -------
    (wip_data, job_data, inter_times, machine_times)
    """
    inter_times, machine_times = _load_distance_matrix(data_dir)

    # ── WIPData 구성 ─────────────────────────────────────────────────────────
    wip_data: Dict[int, WIPData] = {}
    for rec in wip_records:
        loc_name    = (rec.get("loc_name") or "").strip()
        stack_level = int(rec.get("stack_level") or 0)
        width       = float(rec.get("width")  or 0)
        length_     = float(rec.get("length") or 0)

        # 노드명 → stack_id (솔버 내부 ID)
        stack_id = NODE_TO_STACK.get(loc_name, 0)  # 야드 외부 or 미등록 → 0

        wip_data[rec["id"]] = WIPData(
            wip_id     = rec["id"],
            stack_id   = stack_id,
            level      = stack_level,
            short_side = min(width, length_),
            long_side  = max(width, length_),
            thickness  = float(rec.get("thickness") or 0),
            grade      = str(rec.get("material") or ""),
            spec       = (
                f"{rec.get('thickness',0)}"
                f"*{min(width,length_)}"
                f"*{max(width,length_)}"
            ),
            is_output_wip=False,
        )

    # ── JobData 구성 ─────────────────────────────────────────────────────────
    job_data: Dict[int, JobData] = {}
    for rec in cutting_records:
        job_id       = rec["id"]
        input_wip_id = rec.get("steel_wip_id") or 0   # 0 = 원자재
        proc_time    = float(rec.get("estimated_cutting_time") or 1)

        if input_wip_id and input_wip_id in wip_data:
            wip    = wip_data[input_wip_id]
            s_side = wip.short_side
            l_side = wip.long_side
            thick  = wip.thickness
            grade  = wip.grade
        else:
            # 원자재 또는 DB에 없는 WIP — 도면 치수 사용
            raw_w  = float(rec.get("input_width")  or 2438)
            raw_l  = float(rec.get("input_length") or 6096)
            s_side = min(raw_w, raw_l)
            l_side = max(raw_w, raw_l)
            thick  = 12.0
            grade  = str(rec.get("input_material") or "SM355A")

        has_output = bool(rec.get("has_output") and rec.get("output_placeholder_id"))
        out_ph_id  = rec.get("output_placeholder_id") if has_output else None

        job_data[job_id] = JobData(
            job_id           = job_id,
            input_wip_id     = input_wip_id,
            grade            = grade,
            spec             = f"{thick}*{s_side}*{l_side}",
            batch_count      = 1,
            process_time     = proc_time,
            cap_short        = s_side,
            cap_long         = l_side,
            thickness        = thick,
            short_side       = s_side,
            long_side        = l_side,
            generates_output = has_output,
            output_wip_id    = out_ph_id,
            has_external_input = (input_wip_id == 0),
        )

        if has_output and out_ph_id not in wip_data:
            out_w = float(rec.get("output_width") or 0)
            out_l = float(rec.get("output_length") or 0)
            wip_data[out_ph_id] = WIPData(
                wip_id=out_ph_id,
                stack_id=0,
                level=0,
                short_side=min(out_w, out_l),
                long_side=max(out_w, out_l),
                thickness=float(rec.get("output_thickness") or 0),
                grade=str(rec.get("output_material") or grade),
                spec=(
                    f"{float(rec.get('output_thickness') or 0)}"
                    f"*{min(out_w, out_l)}"
                    f"*{max(out_w, out_l)}"
                ),
                is_output_wip=True,
            )

    logger.info(
        "build_solver_input 완료: wips=%d, jobs=%d",
        len(wip_data), len(job_data),
    )
    return wip_data, job_data, inter_times, machine_times


def _load_distance_matrix(
    data_dir: str,
) -> Tuple[Dict[Tuple[str, str], float], Dict[str, float]]:
    """
    distance_matrix_Lazer1.csv 에서 크레인 이동시간을 로드한다.
    CSV 형식: (출발지, 도착지, ..., 이동시간(분, 크레인세팅포함))

    Returns
    -------
    inter_times   : {(src, dst): 이동시간(분)} — 양방향 등록
    machine_times : {node: Lazer-1까지 이동시간(분)}
    """
    path = os.path.join(data_dir, "distance_matrix_Lazer1.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"거리행렬 파일 없음: {path}\n"
            "caasdy/data/distance_matrix_Lazer1.csv 가 배포됐는지 확인하세요."
        )

    COL_TIME = "이동시간(분, 크레인세팅포함)"
    inter_times:   Dict[Tuple[str, str], float] = {}
    machine_times: Dict[str, float]             = {}

    with open(path, encoding="cp949", newline="") as fh:
        for row in csv.DictReader(fh):
            src = row.get("출발지", "").strip()
            dst = row.get("도착지", "").strip()
            try:
                t = float(row[COL_TIME])
            except (ValueError, KeyError):
                continue
            # 양방향 저장
            inter_times[(src, dst)] = t
            inter_times[(dst, src)] = t
            # 설비 이동시간
            if dst == MACHINE_NODE:
                machine_times[src] = t
            elif src == MACHINE_NODE:
                machine_times[dst] = t

    logger.debug(
        "distance_matrix 로드: inter_pairs=%d, machine_nodes=%d",
        len(inter_times), len(machine_times),
    )
    return inter_times, machine_times


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 솔버 실행
# ═══════════════════════════════════════════════════════════════════════════════

def run_caasdy_solver(
    wip_data:      Dict[int, WIPData],
    job_data:      Dict[int, JobData],
    inter_times:   Dict,
    machine_times: Dict,
) -> List[dict]:
    """
    CAASDy 솔버를 실행하고 action log를 반환한다.

    Returns
    -------
    log : List[dict]
        각 엔트리 = {step, action, clock, state_after, ...}
        action.crane.type 이 크레인 행동 타입을 담는다.

    Raises
    ------
    ImportError  : DIDPPy 미설치
    RuntimeError : 솔버 내부 오류
    """
    from .didp.solver import is_available
    if not is_available():
        raise ImportError(
            "DIDPPy가 설치되어 있지 않습니다. "
            "requirements.txt 의 didppy>=0.8.0 을 설치하세요."
        )

    shift_cfg = ShiftConfig(
        manned1_minutes = MANNED1_MINUTES,
        unm1_minutes    = UNM1_MINUTES,
        manned2_minutes = MANNED2_MINUTES,
        unm2_minutes    = UNM2_MINUTES,
    )
    initial_state = build_initial_state(
        wip_data   = wip_data,
        job_data   = job_data,
        buffer_cap = BUFFER_CAP,   # 2 (params.py 에서 설정)
        shift_cfg  = shift_cfg,
    )

    # Phase16 모델 파라미터 (config.py 값 적용)
    model_cfg = CAASDyModelConfig(
        c_rel       = C_REL,
        c_temp      = C_TEMP,
        c_restore   = C_RESTORE,
        r_fill      = R_FILL,
        w_short     = W_SHORT,
        w_long      = W_LONG,
        p_run       = P_RUN,
        p_buffer    = P_BUFFER,
        c_idle_wait = C_IDLE_WAIT,
        c_pre_bonus = C_PRE_BONUS,
    )

    h, tl, bs = HORIZON, TIME_LIMIT, BEAM_SIZE

    def policy_fn(state, wd, jd, mt):
        return rolling_horizon_policy(
            state, wd, jd, mt,
            horizon              = h,
            time_limit           = tl,
            solver_name          = SOLVER_NAME,
            beam_size            = bs,
            verbose              = False,
            model_cfg            = model_cfg,
            allow_greedy_fallback= True,
        )

    logger.info(
        "CAASDy 솔버 시작: jobs=%d, wips=%d, H=%d, TL=%d",
        len(job_data), len(wip_data), h, tl,
    )

    log = run_episode(
        initial_state = initial_state,
        wip_data      = wip_data,
        job_data      = job_data,
        inter_times   = inter_times,
        machine_times = machine_times,
        policy        = policy_fn,
        verbose       = False,
        output_path   = None,
        max_clock     = MAX_CLOCK,
        max_steps     = 0,
    )

    logger.info("CAASDy 솔버 완료: steps=%d", len(log))
    return log


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Action log → BatchItem 생성용 dict 목록
# ═══════════════════════════════════════════════════════════════════════════════

def log_to_batch_plan(
    log:                 List[dict],
    location_map:        Dict[str, int],
    buffer_location_map: Dict[str, int],
    s4_location_map:     Dict[str, int],
    direct_start_wip_map: Optional[Dict[int, int]] = None,
) -> List[dict]:
    """
    action log를 BatchItem 생성에 필요한 dict 목록으로 변환한다.

    WAIT 액션은 기본적으로 제외하지만, DIRECT_START 생산 시작은 별도 이벤트로
    보존해 서비스 응답에서 "원자재 투입"으로 노출할 수 있게 한다.

    S4 슬롯 할당 규칙
    ─────────────────
    PICKING 액션마다 S4 슬롯을 라운드로빈으로 순환 할당한다.
    s4_location_map = {"S4-1": 11, "S4-2": 12, "S4-3": 13, "S4-4": 14}
    순서: S4-1 → S4-2 → S4-3 → S4-4 → S4-1 → …

    RESTORE 원래 스택 보장
    ──────────────────────
    TEMP_MOVE 시 wip_id → origin (node_name, loc_id) 를 origin_stack 에 기록한다.
    버퍼에서 나오는 액션(RESTORE / PRE_POSITION)은 모두 기록된 origin 스택으로만
    이동시킨다. 즉, 버퍼 경유 후 다른 스택으로의 전략 재배치는 금지한다.

    Parameters
    ----------
    log                 : run_caasdy_solver() 반환값
    location_map        : {node_name → DB locations.id}  (야드 스택)
    buffer_location_map : {buffer_name → DB locations.id} (예: {"BUF-1": 15})
    s4_location_map     : {s4_name → DB locations.id}    (예: {"S4-1":11,...})

    Returns
    -------
    List[dict] — 각 item 키:
      job_id           (int|None)
      wip_id           (int|None)
      action           (str)         — BatchActionType 값
      from_location_id (int|None)
      to_location_id   (int|None)
      step             (int)
      clock            (float)       — 시뮬레이션 시각 (분)
    """
    # 야드 + 버퍼 통합 위치 맵
    combined_loc_map = {**location_map, **buffer_location_map}

    # 물리 버퍼는 단일 슬롯 (BUF-1) — map의 첫 번째 값
    buffer_loc_id: Optional[int] = next(iter(buffer_location_map.values()), None)

    # S4 슬롯 라운드로빈 준비
    _s4_names = ["S4-1", "S4-2", "S4-3", "S4-4"]
    _s4_ids   = [s4_location_map.get(n) for n in _s4_names]
    _s4_idx   = 0  # 다음 할당 인덱스

    # TEMP_MOVE 시 origin 스택 기록: {wip_id → (stack_id, node_name, loc_id)}
    origin_stack: Dict[int, Tuple[Optional[int], str, Optional[int]]] = {}

    items: List[dict] = []

    for idx, entry in enumerate(log):
        action      = entry.get("action")
        state_after = entry.get("state_after")
        step        = entry.get("step", 0)
        clock_val   = entry.get("clock")
        if clock_val is None and state_after is not None:
            clock_val = state_after.clock
        clock = float(clock_val or 0.0)

        if action is None:
            continue

        crane = action.crane
        ctype = crane.type

        if ctype == CRANE_WAIT:
            if action.prod.type != PROD_DIRECT_START:
                continue

            direct_job_id = action.prod.job_id
            direct_wip_id = None
            if direct_job_id is not None and direct_start_wip_map:
                direct_wip_id = direct_start_wip_map.get(direct_job_id)

            to_loc_id = _s4_ids[_s4_idx % len(_s4_ids)] if _s4_ids else None
            _s4_idx += 1
            items.append({
                "job_id": direct_job_id,
                "wip_id": direct_wip_id,
                "action": _ACT_DIRECT_START,
                "from_location_id": None,
                "to_location_id": to_loc_id,
                "step": step,
                "clock": clock,
            })
            continue

        batch_action = _CRANE_TO_BATCH.get(ctype)
        if batch_action is None:
            continue

        def has_future_origin_stack_unload(origin_stack_id: Optional[int], current_wip_id: Optional[int]) -> bool:
            if origin_stack_id is None:
                return False
            for future in log[idx + 1 :]:
                future_action = future.get("action")
                if future_action is None:
                    continue
                future_crane = future_action.crane
                if future_crane.type not in {CRANE_PICKING, CRANE_MOVE, CRANE_TEMP_MOVE}:
                    continue
                if future_crane.src_stack != origin_stack_id:
                    continue
                if current_wip_id is not None and future_crane.wip_id == current_wip_id:
                    continue
                return True
            return False

        wip_id = crane.wip_id
        job_id = crane.job_id

        from_loc_id: Optional[int] = None
        to_loc_id:   Optional[int] = None

        if ctype == CRANE_PICKING:
            # 야드 스택 → S4 슬롯 (라운드로빈)
            src_node = STACK_TO_NODE.get(crane.src_stack, "")
            from_loc_id = combined_loc_map.get(src_node)
            # S4 슬롯 할당
            to_loc_id = _s4_ids[_s4_idx % len(_s4_ids)]
            _s4_idx  += 1

        elif ctype == CRANE_STORE:
            # 설비(S4) → 야드 스택
            dst_node    = STACK_TO_NODE.get(crane.dst_stack, "")
            from_loc_id = None   # 설비 위치는 DB location 없음
            to_loc_id   = combined_loc_map.get(dst_node)

        elif ctype == CRANE_MOVE:
            # 야드 → 야드 (영구 재배치)
            src_node    = STACK_TO_NODE.get(crane.src_stack, "")
            dst_node    = STACK_TO_NODE.get(crane.dst_stack, "")
            from_loc_id = combined_loc_map.get(src_node)
            to_loc_id   = combined_loc_map.get(dst_node)

        elif ctype == CRANE_TEMP_MOVE:
            # 야드 스택 → BUF-1 (임시 이동)
            # wip_id의 출발 스택을 기록 → RESTORE 시 원위치 복귀용
            src_node    = STACK_TO_NODE.get(crane.src_stack, "")
            from_loc_id = combined_loc_map.get(src_node)
            to_loc_id   = buffer_loc_id   # 항상 BUF-1
            if wip_id is not None:
                origin_stack[wip_id] = (crane.src_stack, src_node, from_loc_id)

        elif ctype == CRANE_RESTORE:
            from_loc_id = buffer_loc_id
            if wip_id is not None and wip_id in origin_stack:
                _orig_stack_id, _orig_node, _orig_loc_id = origin_stack.pop(wip_id)
                if has_future_origin_stack_unload(_orig_stack_id, wip_id):
                    # 원래 스택 아래에서 아직 추가 언로드가 남아 있으면
                    # 솔버가 선택한 임시 목적지를 유지해야 LIFO가 깨지지 않는다.
                    dst_node = STACK_TO_NODE.get(crane.dst_stack, "")
                    to_loc_id = combined_loc_map.get(dst_node)
                    batch_action = _ACT_RELOCATE
                else:
                    to_loc_id = _orig_loc_id
            else:
                # origin 기록이 없으면 솔버 목적지로 폴백하되, 액션은 RESTORE 유지
                dst_node = STACK_TO_NODE.get(crane.dst_stack, "")
                to_loc_id = combined_loc_map.get(dst_node)

        elif ctype == CRANE_PRE_POSITION:
            # BUF-1에서 빠지는 경우라도, 원래 스택 아래에서 추가 언로드가 남아 있으면
            # 솔버의 임시 목적지를 유지해야 전체 순서가 물리적으로 일관된다.
            from_loc_id = buffer_loc_id
            if wip_id is not None and wip_id in origin_stack:
                _orig_stack_id, _orig_node, _orig_loc_id = origin_stack.pop(wip_id)
                if has_future_origin_stack_unload(_orig_stack_id, wip_id):
                    dst_node = STACK_TO_NODE.get(crane.dst_stack, "")
                    to_loc_id = combined_loc_map.get(dst_node)
                    batch_action = _ACT_RELOCATE
                else:
                    to_loc_id = _orig_loc_id
            else:
                dst_node = STACK_TO_NODE.get(crane.dst_stack, "")
                to_loc_id = combined_loc_map.get(dst_node)

        items.append({
            "job_id"          : job_id,
            "wip_id"          : wip_id,
            "action"          : batch_action,
            "from_location_id": from_loc_id,
            "to_location_id"  : to_loc_id,
            "step"            : step,
            "clock"           : clock,
        })

    logger.info(
        "log_to_batch_plan 완료: total_items=%d (PICKING=%d, INBOUND=%d, "
        "RELOCATE=%d, TEMP_MOVE=%d, RESTORE=%d)",
        len(items),
        sum(1 for i in items if i["action"] == _ACT_PICKING),
        sum(1 for i in items if i["action"] == _ACT_INBOUND),
        sum(1 for i in items if i["action"] == _ACT_RELOCATE),
        sum(1 for i in items if i["action"] == _ACT_TEMP_MOVE),
        sum(1 for i in items if i["action"] == _ACT_RESTORE),
    )
    return items
