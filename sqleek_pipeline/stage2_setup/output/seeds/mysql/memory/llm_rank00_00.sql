-- SQLeek Stage2 MySQL seed: Field::store conversion after column type changes
DROP TABLE IF EXISTS sqleek_s2_field_00;
CREATE TABLE sqleek_s2_field_00 (
  id INT PRIMARY KEY,
  vc VARCHAR(128),
  n DECIMAL(65,30),
  ts TIMESTAMP NULL,
  raw VARBINARY(4096),
  vc_len INT GENERATED ALWAYS AS (CHAR_LENGTH(CAST(vc AS CHAR))) VIRTUAL,
  KEY k_len (vc_len)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_field_00(id, vc, n, ts, raw) VALUES
  (1, 'alpha', 123.456, TIMESTAMP('2001-01-01 00:00:00'), REPEAT(CHAR(65), 32)),
  (2, REPEAT('a', 33), CAST(7 AS DECIMAL(65,30)), NULL, REPEAT(CHAR(65), 32));
INSERT INTO sqleek_s2_field_00(id, vc, n, ts, raw)
  SELECT id + 10, CAST(CONCAT(vc, '_copy') AS CHAR), n * -1, NOW(), CAST(raw AS BINARY) FROM sqleek_s2_field_00;
ALTER TABLE sqleek_s2_field_00 MODIFY vc TEXT, MODIFY n DOUBLE, ADD COLUMN doc JSON NULL;
UPDATE sqleek_s2_field_00
  SET vc = CONCAT(CAST(vc AS CHAR), ':', CAST(n AS CHAR)),
      n = CAST(n AS DECIMAL(30,10)) + 0.5,
      doc = JSON_OBJECT('vc', CAST(vc AS CHAR), 'n', n)
  WHERE id BETWEEN 1 AND 99;
SELECT id, CAST(vc AS CHAR) AS vc2, JSON_EXTRACT(doc, '$.vc') AS jv FROM sqleek_s2_field_00 WHERE vc_len >= 0 ORDER BY vc2, id;
