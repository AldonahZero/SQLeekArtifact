-- SQLeek Stage2 MySQL seed: prepared metadata + generated column + ALTER TABLE
DROP TABLE IF EXISTS sqleek_s2_gen_04;
CREATE TABLE sqleek_s2_gen_04 (
  id INT PRIMARY KEY AUTO_INCREMENT,
  a BIGINT,
  payload VARCHAR(128),
  doc JSON,
  g VARCHAR(255) GENERATED ALWAYS AS (CONCAT(CAST(a AS CHAR), ':', JSON_UNQUOTE(JSON_EXTRACT(doc, '$.k')))) STORED,
  KEY k_g (g),
  KEY k_a (a)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_gen_04(a, payload, doc) VALUES
  (-2147483648, 'json', JSON_OBJECT('k', 'json', 'n', -2147483648)),
  (4096, REPEAT('j', 5), JSON_OBJECT('k', CONCAT('json', '_tail'), 'n', 4096));
SET @sqleek_like := '%json%';
SET @sqleek_lo := -10;
SET @sqleek_hi := 4096;
SET @sqleek_lim := 24;
PREPARE sqleek_ps FROM 'SELECT id, g, CAST(JSON_EXTRACT(doc, "$.k") AS CHAR) AS jk FROM sqleek_s2_gen_04 WHERE g LIKE ? OR a BETWEEN ? AND ? ORDER BY g, id LIMIT ?';
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
ALTER TABLE sqleek_s2_gen_04 MODIFY payload LONGTEXT, ADD COLUMN extra DECIMAL(65,30) DEFAULT -99999.999;
UPDATE sqleek_s2_gen_04 SET payload = CONCAT(CAST(payload AS CHAR), ':', CAST(extra AS CHAR)), doc = JSON_SET(doc, '$.after_alter', extra) WHERE id >= 1;
EXECUTE sqleek_ps USING @sqleek_like, @sqleek_lo, @sqleek_hi, @sqleek_lim;
DEALLOCATE PREPARE sqleek_ps;
