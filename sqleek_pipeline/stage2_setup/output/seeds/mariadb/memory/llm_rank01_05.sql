-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_05;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_05;
CREATE TABLE sqleek_s2_mdb_range_a_05(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_05(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_05 VALUES
  (1, 1, 65535, 'range', 65535.00001),
  (2, 1, 65535 + 1, CONCAT('range', '_range'), 65535.00001 + 1),
  (3, 2, -1, REPEAT('r', LEAST(256, 120)), (-1 * (65535.00001)));
INSERT INTO sqleek_s2_mdb_range_b_05 VALUES
  (1, 1, 'range:left'), (2, 2, 'range:right'), (3, 3, REPEAT('r', LEAST(256, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_05 AS a LEFT JOIN sqleek_s2_mdb_range_b_05 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -65535 AND 65535 + 10)
    OR (a.txt LIKE 'r%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_05 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
