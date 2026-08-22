-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_06;
CREATE TABLE sqleek_s2_gen_06 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_06(a, payload, doc) VALUES
  (9223372036854775807, 'edge', JSON_OBJECT('k', 'edge', 'n', 9223372036854775807)),
  (100000, REPEAT('e', 5), JSON_OBJECT('k', CONCAT('edge', '_tail'), 'n', 100000));
SET @sqleek_like := '%edge%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 100000;
SET @sqleek_lim := 12;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_06 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_06 MODIFY payload TEXT, ADD COLUMN extra DECIMAL(65,30) DEFAULT 123456789.98765;
UPDATE sqleek_s2_gen_06 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
