-- raw_material_specs를 가로/세로 기준 테이블로 단순화
-- 1) 동일 width/length 중복 행 정리
-- 2) material, thickness 컬럼 제거

DELETE r1
FROM raw_material_specs r1
JOIN raw_material_specs r2
  ON r1.id > r2.id
 AND r1.width = r2.width
 AND r1.length = r2.length;

ALTER TABLE raw_material_specs
  DROP COLUMN material,
  DROP COLUMN thickness;
