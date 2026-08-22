-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_00;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_00;
CREATE TABLE sqleek_s2_mdb_range_a_00(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_00(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_00 VALUES
  (1, 1, 7, 'alpha', 123.456),
  (2, 1, 7 + 1, CONCAT('alpha', '_range'), 123.456 + 1),
  (3, 2, -1, REPEAT('a', LEAST(32, 120)), (-1 * (123.456)));
INSERT INTO sqleek_s2_mdb_range_b_00 VALUES
  (1, 1, 'alpha:left'), (2, 2, 'alpha:right'), (3, 3, REPEAT('a', LEAST(32, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_00 AS a LEFT JOIN sqleek_s2_mdb_range_b_00 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -7 AND 7 + 10)
    OR (a.txt LIKE 'a%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_00 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
