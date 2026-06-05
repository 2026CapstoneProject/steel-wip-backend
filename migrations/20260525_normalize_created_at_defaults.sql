-- Normalize created_at defaults to database-safe CURRENT_TIMESTAMP values.
-- Run after reviewing in a maintenance window.

START TRANSACTION;

UPDATE users
SET created_at = UTC_TIMESTAMP()
WHERE created_at IS NULL;

UPDATE scenarios
SET created_at = UTC_TIMESTAMP()
WHERE created_at IS NULL;

UPDATE token_blacklist
SET created_at = UTC_TIMESTAMP()
WHERE created_at IS NULL;

ALTER TABLE users
    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE scenarios
    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE token_blacklist
    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

COMMIT;
