-- SQLeek Stage2 MySQL seed: range optimizer + join + index metadata churn
DROP TABLE IF EXISTS sqleek_s2_range_b_00;
DROP TABLE IF EXISTS sqleek_s2_range_a_00;
CREATE TABLE sqleek_s2_range_a_00 (
  id INT PRIMARY KEY,
  k BIGINT,
  c VARCHAR(256),
  d DECIMAL(65,20),
  KEY k_k (k),
  KEY k_c (c(32))
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_range_b_00 (
  id INT PRIMARY KEY,
  a_id INT,
  v BIGINT,
  note VARCHAR(128),
  KEY k_aid (a_id),
  KEY k_v (v)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_range_a_00 VALUES
  (1, 0, 'alpha', 123.456),
  (2, 7, CONCAT('alpha', '_range'), 123.456 + 1),
  (3, -7, REPEAT('a', 40), -123.456);
INSERT INTO sqleek_s2_range_b_00 VALUES
  (1, 1, 7, 'left'),
  (2, 2, 7 + 1, 'right'),
  (3, 3, -1, 'edge');
SET @lo := -100;
SET @hi := 7;
SET @pat := '%alpha%';
SET @lim := 10;
PREPARE sqleek_range FROM 'SELECT SQL_SMALL_RESULT a.id, b.v, CAST(a.c AS CHAR) AS c2 FROM sqleek_s2_range_a_00 AS a JOIN sqleek_s2_range_b_00 AS b ON b.a_id = a.id WHERE (a.k BETWEEN ? AND ? OR a.c LIKE ?) AND b.v IN (SELECT MAX(v) FROM sqleek_s2_range_b_00 GROUP BY a_id) ORDER BY a.c, b.v LIMIT ?';
EXECUTE sqleek_range USING @lo, @hi, @pat, @lim;
ALTER TABLE sqleek_s2_range_a_00 DROP INDEX k_c, ADD INDEX k_mix (k, id), MODIFY c TEXT;
UPDATE sqleek_s2_range_a_00 SET c = CONCAT(CAST(c AS CHAR), ':', CAST(d AS CHAR)), k = CASE WHEN k >= 9223372036854775806 THEN k ELSE k + 1 END WHERE id IN (1, 2, 3);
EXECUTE sqleek_range USING @lo, @hi, @pat, @lim;
DEALLOCATE PREPARE sqleek_range;
