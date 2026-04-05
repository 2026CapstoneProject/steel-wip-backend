# 08 QR 인식 화면 API — 테스트 결과

## 개요

| 항목 | 내용 |
|------|------|
| 대상 기능 | QR 인식 화면 6개 API (GET 3 + POST 3) |
| 테스트 파일 | `tests/test_field.py` |
| 총 테스트 수 | **16개** |
| 통과 | **16개** |
| 실패 | **0개** |
| 테스트 날짜 | 2026-04-06 |
| 브랜치 | `feature/SY2026-63-inboundqr` |

---

## API 목록

| No | Method | Endpoint | 설명 |
|----|--------|----------|------|
| 1 | GET | `/api/field/{batchItemId}/relocQr` | 재배치 QR 화면 조회 |
| 2 | GET | `/api/field/{batchItemId}/pickingQr` | 피킹 QR 화면 조회 |
| 3 | GET | `/api/field/{batchItemId}/inboundQr` | 적재 QR 화면 조회 |
| 4 | POST | `/api/field/{batchItemId}/wipQR` | 잔재 QR 스캔 |
| 5 | POST | `/api/field/{batchItemId}/locQR` | 위치 QR 스캔 |
| 6 | POST | `/api/field/{batchItemId}` | 저장 (작업 완료 처리) |

---

## 테스트 결과 상세

### GET — 재배치/피킹/적재 QR 화면 조회 (5개)

| # | 테스트명 | 검증 항목 | 결과 |
|---|----------|-----------|------|
| 1 | `test_reloc_qr_basic` | RELOCATE 아이템 조회 시 wipId·material·thickness·width·height(=DB length)·fromLocationName·toLocationName 정확히 반환 | ✅ PASS |
| 2 | `test_reloc_qr_scan_flags_false` | item_scanned_at / destination_scanned_at = null → itemScan=false, destinationScan=false | ✅ PASS |
| 3 | `test_reloc_qr_scan_flags_true` | item_scanned_at / destination_scanned_at 설정됨 → itemScan=true, destinationScan=true | ✅ PASS |
| 4 | `test_picking_qr_to_location_is_lazer_name` | PICKING: toLocationName = scenario.lazer_name (창고 위치가 아닌 레이저 기기명) | ✅ PASS |
| 5 | `test_inbound_qr_from_location_is_lazer_name` | INBOUND: fromLocationName = scenario.lazer_name (창고 위치가 아닌 레이저 기기명) | ✅ PASS |

### POST — 잔재 QR 스캔 (4개)

| # | 테스트명 | 검증 항목 | 결과 |
|---|----------|-----------|------|
| 6 | `test_wip_qr_scan_success` | 올바른 잔재 QR 스캔 → 200, item_scanned_at 업데이트 | ✅ PASS |
| 7 | `test_wip_qr_not_found_batch_item` | 존재하지 않는 batchItemId 요청 → 404 | ✅ PASS |
| 8 | `test_wip_qr_not_found_qr_code` | DB에 없는 QR 코드 스캔 → 400 | ✅ PASS |
| 9 | `test_wip_qr_poka_yoke_fail` | Poka-Yoke: wip1 대상 배치에 wip2 QR 스캔 → 400 | ✅ PASS |

### POST — 위치 QR 스캔 (4개)

| # | 테스트명 | 검증 항목 | 결과 |
|---|----------|-----------|------|
| 10 | `test_loc_qr_scan_success` | 올바른 위치 QR 스캔 → 200, destination_scanned_at 업데이트 | ✅ PASS |
| 11 | `test_loc_qr_not_found_batch_item` | 존재하지 않는 batchItemId 요청 → 404 | ✅ PASS |
| 12 | `test_loc_qr_not_found_location` | DB에 없는 위치명 스캔 → 400 | ✅ PASS |
| 13 | `test_loc_qr_poka_yoke_fail` | Poka-Yoke: to_location=B-1인데 C-1 스캔 → 400 | ✅ PASS |

### POST — 저장 / 작업 완료 처리 (3개)

| # | 테스트명 | 검증 항목 | 결과 |
|---|----------|-----------|------|
| 14 | `test_save_relocation_success` | RELOCATION 저장 → batch_item.status=COMPLETED, steel_wip.location_id=to_location | ✅ PASS |
| 15 | `test_save_inbound_success` | INBOUND 저장 → batch_item.status=COMPLETED, steel_wip.location_id=to_location, status=IN_STOCK | ✅ PASS |
| 16 | `test_save_picking_success` | PICKING 저장 → batch_item.status=COMPLETED, steel_wip.location_id=None (창고 위치 해제) | ✅ PASS |

---

## 핵심 설계 검증 사항

### 1. Poka-Yoke (ポカヨケ) 검증
QR 스캔 시 실제 잔재/위치가 배치 아이템의 기대 대상과 일치하는지 검증한다. 불일치 시 400 에러를 반환하여 작업 오류를 사전 차단한다.

- **잔재 QR**: `QrCodes.qr_code` → `SteelWip` 조회 → `batch_item.steel_wip_id`와 일치 확인
- **위치 QR**: `Locations.loc_name` → 위치 조회 → `batch_item.to_location`과 일치 확인

### 2. PICKING / INBOUND의 특수 위치 처리
| 작업 유형 | from_location | to_location | 비고 |
|-----------|--------------|-------------|------|
| RELOCATE | 창고 위치 (loc_name) | 창고 위치 (loc_name) | 일반 이동 |
| PICKING | 창고 위치 (loc_name) | **null** | 레이저 기기로 투입 |
| INBOUND | **null** | 창고 위치 (loc_name) | 레이저 기기에서 입고 |

- `PICKING`의 `toLocationName` = `scenario.lazer_name`
- `INBOUND`의 `fromLocationName` = `scenario.lazer_name`
- 위치 QR 검증 시 `PICKING`은 `to_location=null`이므로 위치 검증 생략

### 3. 저장(save) 후 SteelWip 상태 변화
| 작업 유형 | location_id 변화 | status 변화 |
|-----------|-----------------|------------|
| RELOCATION | `from → to` | 변경 없음 |
| INBOUND | `None → to_location` | → `IN_STOCK` |
| PICKING | `from → None` | 변경 없음 |

### 4. 라우터 순서 충돌 방지
`/{batchItemId}/subPath` (2 세그먼트)와 `/{lazer_name}` (1 세그먼트) 캐치올은 FastAPI가 경로 세그먼트 수로 구분하므로 충돌하지 않는다. `POST /{batchItemId}` (1 세그먼트)는 HTTP 메서드(POST)가 다르므로 GET 캐치올과 구분된다.

---

## DB 컬럼 매핑 주의사항

| API 응답 필드 | DB 컬럼 | 비고 |
|--------------|---------|------|
| `height` | `steel_wip.length` | 명세서 표기는 height, DB 컬럼명은 length |
| `itemScan` | `batch_item.item_scanned_at is not None` | null 여부로 bool 변환 |
| `destinationScan` | `batch_item.destination_scanned_at is not None` | null 여부로 bool 변환 |

---

## 테스트 환경

- **DB**: SQLite in-memory (aiosqlite) — 매 테스트마다 독립 DB 생성 및 롤백
- **HTTP Client**: httpx AsyncClient (FastAPI TestClient)
- **비동기 엔진**: pytest-asyncio
- **픽스처**: `conftest.py`의 `client`, `db_session` 픽스처 활용
