-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_06;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_06;
CREATE TABLE sqleek_s2_mdb_range_a_06(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_06(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_06 VALUES
  (1, 1, 100000, 'edge', 123456789.98765),
  (2, 1, 100000 + 1, CONCAT('edge', '_range'), 123456789.98765 + 1),
  (3, 2, -1, REPEAT('e', LEAST(384, 120)), (-1 * (123456789.98765)));
INSERT INTO sqleek_s2_mdb_range_b_06 VALUES
  (1, 1, 'edge:left'), (2, 2, 'edge:right'), (3, 3, REPEAT('e', LEAST(384, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_06 AS a LEFT JOIN sqleek_s2_mdb_range_b_06 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -100000 AND 100000 + 10)
    OR (a.txt LIKE 'e%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_06 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
