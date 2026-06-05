"""
Phase13_비교실험 슬롯 레이아웃 유틸리티.

현재 단계에서는 2x2 슬롯 기준으로
- WIP 집합이 배치 가능한지 검사하고
- slot-aware PICKING을 위해 "어느 시작 슬롯에 둘 수 있는지" 계산하고
- 표시용 slot assignment / footprint를 함께 만든다.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from ..data.loader import JobData, WIPData
from ..data.params import ENFORCE_VERTICAL_TWO_SLOT, VERTICAL_TWO_SLOT_RATIO

SLOT_ORDER = ("TL", "TR", "BL", "BR")
VERTICAL_COLUMNS = (("TL", "BL"), ("TR", "BR"))


def empty_slots() -> Dict[str, Optional[int]]:
    return {slot: None for slot in SLOT_ORDER}


def requires_vertical_two_slot(wip: WIPData) -> bool:
    """
    세로 2칸 점유 필요 여부.

    2차 구현에서 raw/material 분류 규칙이 구체화되기 전까지는
    long/short 비율 기준의 근사 규칙을 사용한다.
    """
    if not ENFORCE_VERTICAL_TWO_SLOT:
        return False
    short_side = max(float(wip.short_side), 1e-6)
    ratio = float(wip.long_side) / short_side
    return ratio >= VERTICAL_TWO_SLOT_RATIO


def pack_wips_into_slots(
    wip_ids: Iterable[int],
    wip_data: Dict[int, WIPData],
) -> Tuple[bool, Dict[str, Optional[int]], Dict[int, Tuple[str, ...]]]:
    """
    주어진 WIP 집합이 2x2 슬롯에 배치 가능한지 검사하고,
    가능한 경우 표시용 slot assignment와 footprint를 생성한다.
    """
    slots = empty_slots()
    footprints: Dict[int, Tuple[str, ...]] = {}

    members = [wid for wid in wip_ids if wid in wip_data]
    vertical = []
    single = []
    for wid in members:
        wip = wip_data[wid]
        if requires_vertical_two_slot(wip):
            vertical.append(wid)
        else:
            single.append(wid)

    vertical.sort(key=lambda wid: (-wip_data[wid].long_side, wid))
    single.sort(key=lambda wid: (-wip_data[wid].short_side, -wip_data[wid].long_side, wid))

    for wid in vertical:
        placed = False
        for top, bottom in VERTICAL_COLUMNS:
            if slots[top] is None and slots[bottom] is None:
                slots[top] = wid
                slots[bottom] = wid
                footprints[wid] = (top, bottom)
                placed = True
                break
        if not placed:
            return False, slots, footprints

    for wid in single:
        placed = False
        for slot in SLOT_ORDER:
            if slots[slot] is None:
                slots[slot] = wid
                footprints[wid] = (slot,)
                placed = True
                break
        if not placed:
            return False, slots, footprints

    return True, slots, footprints


def footprint_for_start_slot(
    wip: WIPData,
    start_slot: str,
) -> Optional[Tuple[str, ...]]:
    """
    slot-aware PICKING에서 사용할 footprint를 반환한다.

    규약:
    - 일반 WIP: 어떤 빈 슬롯이든 1칸 점유
    - 세로 2칸 WIP: 시작 슬롯은 TL 또는 TR만 허용
      * TL -> (TL, BL)
      * TR -> (TR, BR)
    """
    if requires_vertical_two_slot(wip):
        if start_slot == "TL":
            return ("TL", "BL")
        if start_slot == "TR":
            return ("TR", "BR")
        return None
    if start_slot not in SLOT_ORDER:
        return None
    return (start_slot,)


def candidate_start_slots_for_wip(
    slots: Dict[str, Optional[int]],
    wip: WIPData,
) -> List[str]:
    """
    현재 mach_slots 상태에서 WIP를 둘 수 있는 시작 슬롯 후보를 반환한다.

    세로 2칸 WIP는 top slot(TL/TR)만 반환한다.
    """
    footprint_candidates: List[str] = []
    if requires_vertical_two_slot(wip):
        for top, bottom in VERTICAL_COLUMNS:
            if slots.get(top) is None and slots.get(bottom) is None:
                footprint_candidates.append(top)
        return footprint_candidates

    return [slot for slot in SLOT_ORDER if slots.get(slot) is None]


def place_wip_in_slots(
    slots: Dict[str, Optional[int]],
    wip_id: int,
    wip: WIPData,
    start_slot: str,
) -> Optional[Tuple[Dict[str, Optional[int]], Tuple[str, ...]]]:
    """
    지정된 시작 슬롯에 WIP를 실제로 배치한 결과를 반환한다.

    반환값:
    - 성공: (new_slots, footprint)
    - 실패: None
    """
    footprint = footprint_for_start_slot(wip, start_slot)
    if footprint is None:
        return None
    for slot in footprint:
        if slots.get(slot) is not None:
            return None

    new_slots = dict(slots)
    for slot in footprint:
        new_slots[slot] = wip_id
    return new_slots, footprint


def is_layout_consistent(
    slots: Dict[str, Optional[int]],
    footprints: Dict[int, Tuple[str, ...]],
    active_wips: Iterable[int],
    wip_data: Dict[int, WIPData],
) -> bool:
    """
    현재 slots/footprints가 active_wips 집합과 일관적인지 검사한다.
    """
    active = set(active_wips)
    if set(footprints.keys()) != active:
        return False

    seen = set()
    for wid, footprint in footprints.items():
        if wid not in wip_data:
            return False
        is_vertical = requires_vertical_two_slot(wip_data[wid])
        if is_vertical and footprint not in VERTICAL_COLUMNS:
            return False
        if (not is_vertical) and len(footprint) != 1:
            return False
        for slot in footprint:
            if slot not in SLOT_ORDER:
                return False
            if slots.get(slot) != wid:
                return False
            if slot in seen:
                return False
            seen.add(slot)

    occupied = {slot for slot, wid in slots.items() if wid is not None and wid != 0}
    return occupied == seen


def _raw_piece_id(job_id: int, piece_idx: int) -> int:
    """
    DIRECT_START 원자재 piece를 trace/UI에서 식별하기 위한 가상 음수 ID.
    """
    return -(job_id * 10 + piece_idx + 1)


def build_raw_job_layout(
    job: JobData,
) -> Tuple[bool, Dict[str, Optional[int]], Dict[int, Tuple[str, ...]]]:
    """
    DIRECT_START 원자재 job을 2x2 슬롯 위에 어떻게 배치할지 계산한다.

    현재 가정:
    - batch_count = 올려야 하는 원자재 piece 수
    - 각 piece는 동일 spec
    - 세로 2칸 필요 여부는 job spec(long/short ratio)로 판정
    """
    pseudo_slots = empty_slots()
    pseudo_footprints: Dict[int, Tuple[str, ...]] = {}

    class _PseudoWip:
        def __init__(self, short_side: float, long_side: float):
            self.short_side = short_side
            self.long_side = long_side

    piece = _PseudoWip(job.short_side, job.long_side)
    piece_count = max(int(job.batch_count), 1)

    if requires_vertical_two_slot(piece):  # type: ignore[arg-type]
        if piece_count > len(VERTICAL_COLUMNS):
            return False, pseudo_slots, pseudo_footprints
        for idx in range(piece_count):
            top, bottom = VERTICAL_COLUMNS[idx]
            raw_id = _raw_piece_id(job.job_id, idx)
            pseudo_slots[top] = raw_id
            pseudo_slots[bottom] = raw_id
            pseudo_footprints[raw_id] = (top, bottom)
        return True, pseudo_slots, pseudo_footprints

    if piece_count > len(SLOT_ORDER):
        return False, pseudo_slots, pseudo_footprints
    for idx in range(piece_count):
        slot = SLOT_ORDER[idx]
        raw_id = _raw_piece_id(job.job_id, idx)
        pseudo_slots[slot] = raw_id
        pseudo_footprints[raw_id] = (slot,)
    return True, pseudo_slots, pseudo_footprints
