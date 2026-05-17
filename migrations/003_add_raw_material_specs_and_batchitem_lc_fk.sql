CREATE TABLE IF NOT EXISTS raw_material_specs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    material VARCHAR(255) NOT NULL,
    thickness FLOAT NOT NULL,
    width FLOAT NOT NULL,
    length FLOAT NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    description VARCHAR(255) NULL
);

CREATE INDEX ix_raw_material_specs_active
ON raw_material_specs (is_active);

ALTER TABLE batch_items
ADD COLUMN lazer_cutting_id INT NULL COMMENT 'DIRECT_START/PICKING/INBOUND 연결 LazerCutting';

ALTER TABLE batch_items
ADD CONSTRAINT batch_items_ibfk_6
FOREIGN KEY (lazer_cutting_id) REFERENCES lazer_cutting(id);

CREATE INDEX ix_batch_items_lazer_cutting_id
ON batch_items (lazer_cutting_id);
