-- Migration 001: batch_items.batch_item_action ENUM에 TEMP_MOVE, RESTORE 추가
-- 적용 시점: CAASDy 솔버 통합 배포 시 (기존 DB가 있는 경우만 필요)
--
-- 신규 DB는 main.py의 create_all이 자동으로 새 ENUM 포함하여 생성하므로 불필요.
-- 기존 DB(이미 batch_items 테이블이 있는 경우)에 이 SQL을 실행하세요.
--
-- 실행 방법:
--   mysql -u <user> -p <db_name> < migrations/001_add_temp_move_restore_enum.sql

ALTER TABLE batch_items
  MODIFY COLUMN batch_item_action
    ENUM('RELOCATE', 'PICKING', 'INBOUND', 'TEMP_MOVE', 'RESTORE')
    NOT NULL;
