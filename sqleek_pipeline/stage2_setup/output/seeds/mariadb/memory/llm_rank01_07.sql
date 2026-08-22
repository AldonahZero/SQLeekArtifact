-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_07;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_07;
CREATE TABLE sqleek_s2_mdb_range_a_07(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_07(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_07 VALUES
  (1, 1, 2048, 'nullish', 0.0000001),
  (2, 1, 2048 + 1, CONCAT('nullish', '_range'), 0.0000001 + 1),
  (3, 2, -1, REPEAT('n', LEAST(512, 120)), (-1 * (0.0000001)));
INSERT INTO sqleek_s2_mdb_range_b_07 VALUES
  (1, 1, 'nullish:left'), (2, 2, 'nullish:right'), (3, 3, REPEAT('n', LEAST(512, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_07 AS a LEFT JOIN sqleek_s2_mdb_range_b_07 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -2048 AND 2048 + 10)
    OR (a.txt LIKE 'n%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_07 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
