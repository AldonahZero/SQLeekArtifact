-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_01;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_01;
CREATE TABLE sqleek_s2_mdb_range_a_01(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_01(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_01 VALUES
  (1, 1, 64, 'beta', -999999.0001),
  (2, 1, 64 + 1, CONCAT('beta', '_range'), -999999.0001 + 1),
  (3, 2, -1, REPEAT('b', LEAST(64, 120)), (-1 * (-999999.0001)));
INSERT INTO sqleek_s2_mdb_range_b_01 VALUES
  (1, 1, 'beta:left'), (2, 2, 'beta:right'), (3, 3, REPEAT('b', LEAST(64, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_01 AS a LEFT JOIN sqleek_s2_mdb_range_b_01 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -64 AND 64 + 10)
    OR (a.txt LIKE 'b%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_01 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
