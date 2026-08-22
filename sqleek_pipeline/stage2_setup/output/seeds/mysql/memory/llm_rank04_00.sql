-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_00;
CREATE TABLE sqleek_s2_gen_00 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_00(a, payload, doc) VALUES
  (0, 'alpha', JSON_OBJECT('k', 'alpha', 'n', 0)),
  (7, REPEAT('a', 5), JSON_OBJECT('k', CONCAT('alpha', '_tail'), 'n', 7));
SET @sqleek_like := '%alpha%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 7;
SET @sqleek_lim := 10;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_00 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_00 MODIFY payload TEXT, ADD COLUMN extra DECIMAL(65,30) DEFAULT 123.456;
UPDATE sqleek_s2_gen_00 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
