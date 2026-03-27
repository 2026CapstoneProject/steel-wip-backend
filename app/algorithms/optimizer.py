# 피킹/적재 최적화 알고리즘 모듈
# 작업지시서 해석 결과와 현재 잔재 재고 위치 정보를 기반으로
# 피킹 순서 및 적재 위치를 결정합니다.


def run_optimizer(wips: list, work_order: dict) -> dict:
    """
    잔재 피킹/적재 최적화 알고리즘 진입점

    Args:
        wips: 현재 DB에 저장된 IN_STOCK 상태 잔재 목록
        work_order: 작업지시서 해석 결과
            {
                "picking_targets": [...],   # 피킹 대상 잔재 규격
                "inbound_targets": [...],   # 생산 후 입고 예정 잔재 규격
                "due_date": "...",
                "lazer_name": "..."
            }

    Returns:
        시나리오 스텝 구성 결과 dict
    """
    # TODO: 알고리즘 구현
    raise NotImplementedError("optimizer 알고리즘이 아직 구현되지 않았습니다.")
