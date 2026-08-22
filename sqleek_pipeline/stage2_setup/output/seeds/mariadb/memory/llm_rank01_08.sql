-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_08;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_08;
CREATE TABLE sqleek_s2_mdb_range_a_08(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_08(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_08 VALUES
  (1, 1, 8192, 'sort', 314159.26535),
  (2, 1, 8192 + 1, CONCAT('sort', '_range'), 314159.26535 + 1),
  (3, 2, -1, REPEAT('s', LEAST(768, 120)), (-1 * (314159.26535)));
INSERT INTO sqleek_s2_mdb_range_b_08 VALUES
  (1, 1, 'sort:left'), (2, 2, 'sort:right'), (3, 3, REPEAT('s', LEAST(768, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_08 AS a LEFT JOIN sqleek_s2_mdb_range_b_08 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -8192 AND 8192 + 10)
    OR (a.txt LIKE 's%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_08 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
