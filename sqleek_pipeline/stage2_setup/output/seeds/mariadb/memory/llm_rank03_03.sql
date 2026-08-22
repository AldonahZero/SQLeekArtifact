-- SQLeek Stage2 MariaDB seed: temporary table copy + blob/text metadata
DROP TEMPORARY TABLE IF EXISTS sqleek_s2_mdb_tmp_work_03;
DROP TABLE IF EXISTS sqleek_s2_mdb_tmp_03;
CREATE TABLE sqleek_s2_mdb_tmp_03(
  id INT PRIMARY KEY,
  bucket INT,
  txt TEXT,
  raw VARBINARY(4096),
  KEY k_bucket (bucket)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_tmp_03 VALUES
  (1, 1, 'wide', REPEAT(CHAR(65), 128)),
  (2, 1, REPEAT('w', LEAST(128, 1024)), REPEAT(CHAR(66), 128)),
  (3, 2, CONCAT('wide', '_tail'), REPEAT(CHAR(67), 128));
CREATE TEMPORARY TABLE sqleek_s2_mdb_tmp_work_03 AS
  SELECT bucket, CAST(txt AS CHAR(255)) AS txt2, COUNT(*) AS cnt, SUM(OCTET_LENGTH(raw)) AS bytes
  FROM sqleek_s2_mdb_tmp_03 GROUP BY bucket, txt2 ORDER BY txt2;
ALTER TABLE sqleek_s2_mdb_tmp_work_03 MODIFY txt2 TEXT, ADD INDEX k_txt2 (txt2(32));
UPDATE sqleek_s2_mdb_tmp_work_03 AS w JOIN sqleek_s2_mdb_tmp_03 AS b ON b.bucket = w.bucket
  SET w.txt2 = CONCAT(CAST(w.txt2 AS CHAR), ':', CAST(b.id AS CHAR)), w.cnt = w.cnt + 1;
SELECT bucket, GROUP_CONCAT(CAST(txt2 AS CHAR) ORDER BY txt2 SEPARATOR '|') AS packed, SUM(bytes) AS total_bytes
  FROM sqleek_s2_mdb_tmp_work_03
 GROUP BY bucket
 HAVING total_bytes >= 0
 ORDER BY packed;
DROP TEMPORARY TABLE IF EXISTS sqleek_s2_mdb_tmp_work_03;
