-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: Add estimated_wip_id column to batch_items table
-- Purpose: Connect INBOUND BatchItems to EstimatedWips for material spec tracking
-- Status: Ready for execution
-- Created: 2026-05-17
-- ─────────────────────────────────────────────────────────────────────────────

-- Step 1: Add the estimated_wip_id column
ALTER TABLE batch_items
ADD COLUMN estimated_wip_id INT DEFAULT NULL
COMMENT 'INBOUND 시 EstimatedWips 참조'
AFTER steel_wip_id;

-- Step 2: Add foreign key constraint (connects INBOUND to EstimatedWips)
ALTER TABLE batch_items
ADD CONSTRAINT batch_items_ibfk_5
FOREIGN KEY (estimated_wip_id)
REFERENCES estimated_wips(id)
ON DELETE SET NULL
ON UPDATE CASCADE;

-- Step 3: Add index for query performance
ALTER TABLE batch_items
ADD INDEX ix_batch_items_estimated_wip_id (estimated_wip_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Verification Queries (run after migration to verify success)
-- ─────────────────────────────────────────────────────────────────────────────

-- Check if column was added
-- SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT
-- FROM INFORMATION_SCHEMA.COLUMNS
-- WHERE TABLE_NAME = 'batch_items'
-- AND TABLE_SCHEMA = 'steel_wip_db'
-- AND COLUMN_NAME = 'estimated_wip_id';

-- Check if foreign key was added
-- SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
-- FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
-- WHERE TABLE_NAME = 'batch_items'
-- AND TABLE_SCHEMA = 'steel_wip_db'
-- AND COLUMN_NAME = 'estimated_wip_id';

-- Check if index was added
-- SHOW INDEXES FROM batch_items WHERE Column_name = 'estimated_wip_id';
