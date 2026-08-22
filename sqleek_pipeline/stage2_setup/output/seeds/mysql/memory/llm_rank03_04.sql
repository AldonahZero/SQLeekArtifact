-- SQLeek Stage2 MySQL seed: temporary table copy + filesort + blob/text metadata churn
DROP TEMPORARY TABLE IF EXISTS sqleek_s2_tmp_work_04;
DROP TABLE IF EXISTS sqleek_s2_tmp_04;
CREATE TABLE sqleek_s2_tmp_04 (
  id INT PRIMARY KEY,
  bucket INT,
  txt TEXT,
  raw VARBINARY(4096),
  KEY k_bucket (bucket)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_tmp_04 VALUES
  (1, 1, 'json', REPEAT(CHAR(69), 512)),
  (2, 1, REPEAT('j', 70), REPEAT(CHAR(69), 512)),
  (3, 2, CONCAT('json', '_tail'), REPEAT(CHAR(69), 512));
CREATE TEMPORARY TABLE sqleek_s2_tmp_work_04 AS
  SELECT bucket, CAST(txt AS CHAR(255)) AS txt2, COUNT(*) AS cnt, SUM(OCTET_LENGTH(raw)) AS bytes
  FROM sqleek_s2_tmp_04 GROUP BY bucket, txt2 ORDER BY txt2;
ALTER TABLE sqleek_s2_tmp_work_04 MODIFY txt2 LONGTEXT, ADD INDEX k_txt2 (txt2(32));
UPDATE sqleek_s2_tmp_work_04 AS w JOIN sqleek_s2_tmp_04 AS base ON base.bucket = w.bucket
  SET w.txt2 = CONCAT(CAST(w.txt2 AS CHAR), ':', CAST(base.id AS CHAR)), w.cnt = w.cnt + 1;
SELECT bucket, GROUP_CONCAT(CAST(txt2 AS CHAR) ORDER BY txt2 SEPARATOR '|') AS packed, SUM(bytes) AS total_bytes
  FROM sqleek_s2_tmp_work_04 GROUP BY bucket HAVING total_bytes >= 0 ORDER BY packed;
DROP TEMPORARY TABLE sqleek_s2_tmp_work_04;
