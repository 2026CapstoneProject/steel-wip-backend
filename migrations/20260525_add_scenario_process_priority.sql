ALTER TABLE `scenarios`
  ADD COLUMN `process_priority` ENUM('LOW', 'MIDDLE', 'HIGH') NULL DEFAULT 'LOW'
  AFTER `lazer_name`;

UPDATE `scenarios`
SET `process_priority` = 'LOW'
WHERE `process_priority` IS NULL;
