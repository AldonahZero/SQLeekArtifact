-- SQLeek Stage2 MariaDB seed: Field::store conversion + metadata churn
DROP TABLE IF EXISTS sqleek_s2_mdb_field_06;
CREATE TABLE sqleek_s2_mdb_field_06 (
  id INT PRIMARY KEY,
  vc VARCHAR(160),
  n DECIMAL(65,30),
  ts DATETIME NULL,
  raw VARBINARY(4096),
  KEY k_vc (vc),
  KEY k_n (n)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_field_06(id, vc, n, ts, raw) VALUES
  (1, 'edge', 123456789.98765, '2001-01-01 00:00:00', REPEAT(CHAR(65), 384)),
  (2, REPEAT('e', 384), CAST(100000 AS DECIMAL(65,30)), NULL, REPEAT(CHAR(66), 384));
INSERT INTO sqleek_s2_mdb_field_06
  SELECT id + 10, CONCAT(vc, '_copy'), n * -1, NOW(), CAST(raw AS BINARY) FROM sqleek_s2_mdb_field_06;
ALTER TABLE sqleek_s2_mdb_field_06 MODIFY vc TEXT, MODIFY n DOUBLE, ADD COLUMN extra VARCHAR(255) NULL;
UPDATE sqleek_s2_mdb_field_06
  SET vc = CONCAT(CAST(vc AS CHAR), ':', CAST(n AS CHAR)),
      n = CAST(n AS DECIMAL(30,10)) + 0.5,
      extra = CONCAT(CAST(id AS CHAR), ':', COALESCE(CAST(ts AS CHAR), 'null'))
  WHERE id BETWEEN 1 AND 99;
SELECT id, CAST(vc AS CHAR) AS vc2, CAST(n AS DECIMAL(30,8)) AS nd, extra
  FROM sqleek_s2_mdb_field_06
 WHERE CHAR_LENGTH(CAST(vc AS CHAR)) >= 0
 ORDER BY vc2, nd, id;
