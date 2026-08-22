-- SQLeek Stage2 MariaDB seed: Field::store conversion + metadata churn
DROP TABLE IF EXISTS sqleek_s2_mdb_field_07;
CREATE TABLE sqleek_s2_mdb_field_07 (
  id INT PRIMARY KEY,
  vc VARCHAR(160),
  n DECIMAL(65,30),
  ts DATETIME NULL,
  raw VARBINARY(4096),
  KEY k_vc (vc),
  KEY k_n (n)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_field_07(id, vc, n, ts, raw) VALUES
  (1, 'nullish', 0.0000001, '2001-01-01 00:00:00', REPEAT(CHAR(65), 512)),
  (2, REPEAT('n', 512), CAST(2048 AS DECIMAL(65,30)), NULL, REPEAT(CHAR(66), 512));
INSERT INTO sqleek_s2_mdb_field_07
  SELECT id + 10, CONCAT(vc, '_copy'), n * -1, NOW(), CAST(raw AS BINARY) FROM sqleek_s2_mdb_field_07;
ALTER TABLE sqleek_s2_mdb_field_07 MODIFY vc TEXT, MODIFY n DOUBLE, ADD COLUMN extra VARCHAR(255) NULL;
UPDATE sqleek_s2_mdb_field_07
  SET vc = CONCAT(CAST(vc AS CHAR), ':', CAST(n AS CHAR)),
      n = CAST(n AS DECIMAL(30,10)) + 0.5,
      extra = CONCAT(CAST(id AS CHAR), ':', COALESCE(CAST(ts AS CHAR), 'null'))
  WHERE id BETWEEN 1 AND 99;
SELECT id, CAST(vc AS CHAR) AS vc2, CAST(n AS DECIMAL(30,8)) AS nd, extra
  FROM sqleek_s2_mdb_field_07
 WHERE CHAR_LENGTH(CAST(vc AS CHAR)) >= 0
 ORDER BY vc2, nd, id;
