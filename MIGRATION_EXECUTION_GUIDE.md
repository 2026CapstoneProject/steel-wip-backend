# INBOUND EstimatedWips FK 마이그레이션 실행 가이드

## 📋 마이그레이션 개요

**파일:** `migrations/002_add_estimated_wip_fk.sql`

**목적:** INBOUND 액션의 발생 재공품(EstimatedWips) 정보 추적

**변경사항:**
- `batch_items` 테이블에 `estimated_wip_id` 컬럼 추가
- EstimatedWips 테이블과의 외래키 관계 설정
- 조회 성능 최적화를 위한 인덱스 추가

---

## 🚀 마이그레이션 실행 방법

### ✅ 방법 1: MySQL 커맨드라인 (권장 - 가장 간단)

```bash
# 1. MySQL 접속
mysql -u root -p steel_wip_db

# 2. SQL 파일 실행
source /path/to/steel-wip-backend/migrations/002_add_estimated_wip_fk.sql

# 3. 마이그레이션 확인 (아래 참고)
```

**경로 예시:**
```bash
# Mac/Linux의 경우
source ~/Projects/steel-wip-backend/migrations/002_add_estimated_wip_fk.sql

# Windows의 경우
source C:\Users\YourName\Projects\steel-wip-backend\migrations\002_add_estimated_wip_fk.sql
```

---

### ✅ 방법 2: Python 마이그레이션 스크립트

```bash
# 백엔드 디렉토리로 이동
cd steel-wip-backend

# Python 스크립트 실행
python3 migrate_estimated_wip.py
```

**필요 조건:**
- 데이터베이스가 실행 중이어야 함
- `.env` 파일에 올바른 DB 자격증명이 있어야 함
- Python 의존성이 설치되어 있어야 함

---

### ✅ 방법 3: Docker/DB Manager를 통한 실행

MySQL Workbench, DataGrip, 또는 DBeaver를 사용 중인 경우:

1. 데이터베이스에 연결
2. 쿼리 창 열기
3. `migrations/002_add_estimated_wip_fk.sql` 파일 내용 복사
4. 쿼리 실행 (Ctrl+Enter 또는 버튼)

---

## ✓ 마이그레이션 확인

실행 후 다음 쿼리들을 통해 마이그레이션이 성공했는지 확인하세요:

### 1️⃣ 컬럼이 추가되었는지 확인

```sql
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'batch_items'
AND TABLE_SCHEMA = 'steel_wip_db'
AND COLUMN_NAME = 'estimated_wip_id';
```

**예상 결과:**
```
COLUMN_NAME: estimated_wip_id
COLUMN_TYPE: int
IS_NULLABLE: YES
COLUMN_COMMENT: INBOUND 시 EstimatedWips 참조
```

### 2️⃣ 외래키 제약이 추가되었는지 확인

```sql
SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
WHERE TABLE_NAME = 'batch_items'
AND TABLE_SCHEMA = 'steel_wip_db'
AND COLUMN_NAME = 'estimated_wip_id';
```

**예상 결과:**
```
CONSTRAINT_NAME: batch_items_ibfk_5
TABLE_NAME: batch_items
COLUMN_NAME: estimated_wip_id
REFERENCED_TABLE_NAME: estimated_wips
REFERENCED_COLUMN_NAME: id
```

### 3️⃣ 인덱스가 추가되었는지 확인

```sql
SHOW INDEXES FROM batch_items WHERE Column_name = 'estimated_wip_id';
```

**예상 결과:**
```
Key_name: ix_batch_items_estimated_wip_id
Column_name: estimated_wip_id
```

---

## 🔄 롤백 방법 (문제 발생 시)

문제가 발생한 경우 다음 명령어로 마이그레이션을 롤백할 수 있습니다:

```sql
-- 인덱스 제거
ALTER TABLE batch_items DROP INDEX ix_batch_items_estimated_wip_id;

-- 외래키 제거
ALTER TABLE batch_items DROP CONSTRAINT batch_items_ibfk_5;

-- 컬럼 제거
ALTER TABLE batch_items DROP COLUMN estimated_wip_id;
```

---

## 📊 마이그레이션 후 다음 단계

마이그레이션이 완료된 후:

### 1️⃣ LANTEK 파일 업로드 테스트

```bash
# 백엔드 서버 시작
cd steel-wip-backend
python3 main.py
```

테스트 LANTEK 파일을 업로드하여 다음을 확인하세요:
- ✅ INBOUND 액션이 성공적으로 생성됨
- ✅ `estimated_wip_id`가 EstimatedWips.id로 설정됨
- ✅ FK 제약 위반 오류가 없음

### 2️⃣ 데이터 검증

```sql
-- INBOUND 항목에 estimated_wip_id가 저장되었는지 확인
SELECT 
    bi.id as batch_item_id,
    bi.batch_item_action,
    bi.steel_wip_id,
    bi.estimated_wip_id,
    ew.material,
    ew.thickness,
    ew.width,
    ew.length
FROM batch_items bi
LEFT JOIN estimated_wips ew ON bi.estimated_wip_id = ew.id
WHERE bi.batch_item_action = 'INBOUND'
LIMIT 10;
```

**예상 결과:**
- `estimated_wip_id`가 NULL이 아님
- EstimatedWips 데이터(material, thickness, width, length)가 조인됨

### 3️⃣ 프론트엔드 업데이트 준비

마이그레이션이 완료되면 프론트엔드에서:
- INBOUND 행에 EstimatedWips의 재료 규격 표시 가능
- 현장 조회 페이지에서 적재될 재공품의 상세 정보 표시

---

## ❓ 문제 해결

### 문제: "Column 'estimated_wip_id' already exists"
```
→ 이미 마이그레이션이 실행되었습니다.
→ SHOW COLUMNS FROM batch_items; 로 확인하면 컬럼이 존재합니다.
```

### 문제: "Foreign key constraint fails"
```
→ 해결책:
  1. estimated_wips 테이블이 존재하는지 확인
  2. batch_items 테이블의 기존 데이터에 invalid한 estimated_wip_id가 없는지 확인
  3. 롤백 후 해당 데이터 정리 후 재시도
```

### 문제: "Access denied for user"
```
→ MySQL 사용자 자격증명 확인
→ .env 파일의 DB_USER, DB_PASSWORD 확인
→ 해당 사용자가 ALTER TABLE 권한을 가지고 있는지 확인
```

---

## 📝 체크리스트

- [ ] 데이터베이스 백업 생성
- [ ] 위의 3가지 방법 중 하나로 마이그레이션 실행
- [ ] 3개의 확인 쿼리를 통해 마이그레이션 성공 검증
- [ ] LANTEK 파일 업로드 테스트
- [ ] INBOUND 항목 데이터 검증
- [ ] 프론트엔드 테스트 (EstimatedWips 정보 표시)

---

## 📚 관련 파일

| 파일 | 설명 |
|-----|-----|
| `migrations/002_add_estimated_wip_fk.sql` | SQL 마이그레이션 스크립트 |
| `migrate_estimated_wip.py` | Python 마이그레이션 스크립트 |
| `app/models.py` | SQLAlchemy 모델 (이미 수정됨) |
| `app/algorithms/caasdy_adapter.py` | INBOUND 항목 생성 로직 (이미 수정됨) |
| `MIGRATION_ESTIMATED_WIP_FK.md` | 상세 마이그레이션 기술 문서 |

---

**마이그레이션 생성일:** 2026-05-17
**상태:** 🟡 실행 대기 중 (DB 서버에서 실행 필요)
