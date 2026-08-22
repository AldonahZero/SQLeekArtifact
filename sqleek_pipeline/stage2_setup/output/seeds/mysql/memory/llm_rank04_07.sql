-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_07;
CREATE TABLE sqleek_s2_gen_07 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_07(a, payload, doc) VALUES
  (NULL, 'null', JSON_OBJECT('k', 'null', 'n', 0)),
  (2048, REPEAT('n', 5), JSON_OBJECT('k', CONCAT('null', '_tail'), 'n', 2048));
SET @sqleek_like := '%null%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 2048;
SET @sqleek_lim := 8;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_07 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_07 MODIFY payload MEDIUMBLOB, ADD COLUMN extra DECIMAL(65,30) DEFAULT 0.0000001;
UPDATE sqleek_s2_gen_07 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
