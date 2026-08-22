-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_03;
CREATE TABLE sqleek_s2_gen_03 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_03(a, payload, doc) VALUES
  (2147483647, 'wide', JSON_OBJECT('k', 'wide', 'n', 2147483647)),
  (1024, REPEAT('w', 5), JSON_OBJECT('k', CONCAT('wide', '_tail'), 'n', 1024));
SET @sqleek_like := '%wide%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 1024;
SET @sqleek_lim := 20;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_03 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_03 MODIFY payload BLOB, ADD COLUMN extra DECIMAL(65,30) DEFAULT 21474836.47;
UPDATE sqleek_s2_gen_03 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
