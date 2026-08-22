-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_02;
CREATE TABLE sqleek_s2_gen_02 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_02(a, payload, doc) VALUES
  (-1, 'mix', JSON_OBJECT('k', 'mix', 'n', -1)),
  (255, REPEAT('m', 5), JSON_OBJECT('k', CONCAT('mix', '_tail'), 'n', 255));
SET @sqleek_like := '%mix%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 255;
SET @sqleek_lim := 32;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_02 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_02 MODIFY payload VARCHAR(512), ADD COLUMN extra DECIMAL(65,30) DEFAULT -42.125;
UPDATE sqleek_s2_gen_02 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
