-- SQLeek Stage2 MySQL seed: temporary table copy + filesort + blob/text metadata churn
DROP TEMPORARY TABLE IF EXISTS sqleek_s2_tmp_work_08;
DROP TABLE IF EXISTS sqleek_s2_tmp_08;
CREATE TABLE sqleek_s2_tmp_08 (
  id INT PRIMARY KEY,
  bucket INT,
  txt TEXT,
  raw VARBINARY(4096),
  KEY k_bucket (bucket)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_tmp_08 VALUES
  (1, 1, 'sort', REPEAT(CHAR(71), 2048)),
  (2, 1, REPEAT('s', 70), REPEAT(CHAR(71), 2048)),
  (3, 2, CONCAT('sort', '_tail'), REPEAT(CHAR(71), 2048));
CREATE TEMPORARY TABLE sqleek_s2_tmp_work_08 AS
  SELECT bucket, CAST(txt AS CHAR(255)) AS txt2, COUNT(*) AS cnt, SUM(OCTET_LENGTH(raw)) AS bytes
  FROM sqleek_s2_tmp_08 GROUP BY bucket, txt2 ORDER BY txt2;
ALTER TABLE sqleek_s2_tmp_work_08 MODIFY txt2 VARCHAR(1024), ADD INDEX k_txt2 (txt2(32));
UPDATE sqleek_s2_tmp_work_08 AS w JOIN sqleek_s2_tmp_08 AS base ON base.bucket = w.bucket
  SET w.txt2 = CONCAT(CAST(w.txt2 AS CHAR), ':', CAST(base.id AS CHAR)), w.cnt = w.cnt + 1;
SELECT bucket, GROUP_CONCAT(CAST(txt2 AS CHAR) ORDER BY txt2 SEPARATOR '|') AS packed, SUM(bytes) AS total_bytes
  FROM sqleek_s2_tmp_work_08 GROUP BY bucket HAVING total_bytes >= 0 ORDER BY packed;
DROP TEMPORARY TABLE sqleek_s2_tmp_work_08;
