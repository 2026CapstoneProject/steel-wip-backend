# QR 인식 화면 API — 개발 계획 및 테스트 계획

## 1. 개요

| 항목 | 내용 |
|------|------|
| 화면명 | 08 QR 인식 화면 (APP 현장 담당자) |
| 브랜치 | `feature/SY2026-63-inboundqr` |
| 엔드포인트 수 | **6개** (GET 3개, POST 3개) |
| 대상 파일 | `app/schemas/field.py`, `app/services/field_service.py`, `app/routers/field.py`, `tests/test_field.py` |

---

## 2. 대상 API 목록

| # | 메서드 | 엔드포인트 | 기능 |
|---|--------|------------|------|
| 1 | GET  | `/api/field/{batchItemId}/relocQr`   | 재배치 QR 화면 접속 시 조회 |
| 2 | GET  | `/api/field/{batchItemId}/pickingQr` | 피킹 QR 화면 접속 시 조회 |
| 3 | GET  | `/api/field/{batchItemId}/inboundQr` | 적재 QR 화면 접속 시 조회 |
| 4 | POST | `/api/field/{batchItemId}/wipQR`     | 잔재 QR 스캔 처리 |
| 5 | POST | `/api/field/{batchItemId}/locQR`     | 위치 QR 스캔 처리 |
| 6 | POST | `/api/field/{batchItemId}`           | QR 저장 버튼 (작업 완료 처리) |

---

## 3. 도메인 이해 — QR 인식 화면의 역할

현장직이 실제 작업을 수행할 때 Poka-Yoke(실수 방지) 목적으로 QR을 스캔하는 흐름이다.

```
[현장직 작업 흐름]
  1. 생산 준비 화면에서 작업할 batchItem 선택
  2. QR 인식 화면 접속 (GET — 현재 스캔 상태 확인)
  3. 잔재 QR 스캔    (POST wipQR — item_scanned_at 기록)
  4. 위치 QR 스캔    (POST locQR — destination_scanned_at 기록)
  5. 저장 버튼 클릭  (POST /{batchItemId} — 완료 처리 + DB 반영)
```

### 작업 유형별 from/to 의미

| 작업 | fromLocationName | toLocationName |
|------|-----------------|----------------|
| RELOCATE | `from_location.loc_name` | `to_location.loc_name` |
| PICKING  | `from_location.loc_name` | `scenario.lazer_name` (레이저 기기명) |
| INBOUND  | `scenario.lazer_name` (레이저 기기명) | `to_location.loc_name` |

---

## 4. 응답/요청 스키마

### GET 응답 — QrScanData (3종 공통)

```json
{
  "status": 200,
  "message": "현장 스캔 정보 조회에 성공했습니다.",
  "data": [{
    "batchItemId": 1,
    "wipId": 32,
    "material": "SM355A",
    "thickness": 18.0,
    "width": 2438.0,
    "height": 6096.0,
    "fromLocationName": "A-2",
    "toLocationName": "B-1",
    "itemScan": true,
    "destinationScan": false
  }]
}
```

### POST 요청 스키마

| 엔드포인트 | 요청 필드 |
|------------|----------|
| `POST /wipQR` | `wipQr`, `qrAction` |
| `POST /locQR` | `locQr`, `qrAction` |
| `POST /{batchItemId}` | `action`, `wipQR`, `locQR` |

`qrAction`/`action` 허용값: `"RELOCATION"` | `"INBOUND"` | `"PICKING"`

---

## 5. 비즈니스 로직

### GET 공통 헬퍼 `_get_qr_scan_data(action_type)`

- RELOCATE: from=보관위치, to=보관위치
- PICKING: from=보관위치, to=`scenario.lazer_name`
- INBOUND: from=`scenario.lazer_name`, to=보관위치

### POST wipQR — Poka-Yoke 검증

```
QrCodes.qr_code == wipQr → SteelWip.qr_id → wip.id == batch_item.steel_wip_id
```

### POST locQR — Poka-Yoke 검증

```
PICKING 제외: Locations.loc_name == locQr → loc.id == batch_item.to_location
```

### POST save — 완료 처리

| action | steel_wip 변경사항 |
|--------|-------------------|
| RELOCATION | `location_id = to_location` |
| INBOUND | `location_id = to_location`, `status = "IN_STOCK"` |
| PICKING | `location_id = None` (레이저 투입) |

---

## 6. 라우터 순서 (캐치올 충돌 방지)

```
GET  /end
GET  /progress
GET  /ready
GET  /{batchItemId}/relocQr     ← 신규 (2세그먼트, 캐치올과 충돌 없음)
GET  /{batchItemId}/pickingQr   ← 신규
GET  /{batchItemId}/inboundQr   ← 신규
POST /{batchItemId}/wipQR       ← 신규 (POST, GET 캐치올과 충돌 없음)
POST /{batchItemId}/locQR       ← 신규
POST /{batchItemId}             ← 신규 (POST)
GET  /{lazer_name}              ← 기존 캐치올, 반드시 최하단
```

---

## 7. 테스트 계획 (16개 단위 테스트)

| 구분 | 테스트명 | 검증 |
|------|----------|------|
| GET (5개) | `test_reloc_qr_basic` | wipId·치수·위치 반환 |
| | `test_reloc_qr_scan_flags_false` | null → false |
| | `test_reloc_qr_scan_flags_true` | 설정 → true |
| | `test_picking_qr_to_location_is_lazer_name` | toLocation = lazer_name |
| | `test_inbound_qr_from_location_is_lazer_name` | fromLocation = lazer_name |
| wipQR (4개) | `test_wip_qr_scan_success` | 성공·item_scanned_at 업데이트 |
| | `test_wip_qr_not_found_batch_item` | 404 |
| | `test_wip_qr_not_found_qr_code` | 400 |
| | `test_wip_qr_poka_yoke_fail` | 400 (다른 wip QR) |
| locQR (4개) | `test_loc_qr_scan_success` | 성공·destination_scanned_at 업데이트 |
| | `test_loc_qr_not_found_batch_item` | 404 |
| | `test_loc_qr_not_found_location` | 400 |
| | `test_loc_qr_poka_yoke_fail` | 400 (다른 위치) |
| save (3개) | `test_save_relocation_success` | COMPLETED·location_id 갱신 |
| | `test_save_inbound_success` | COMPLETED·location_id·status=IN_STOCK |
| | `test_save_picking_success` | COMPLETED·location_id=None |
