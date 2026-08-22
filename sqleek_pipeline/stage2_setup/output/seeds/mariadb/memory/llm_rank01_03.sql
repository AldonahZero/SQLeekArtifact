-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_03;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_03;
CREATE TABLE sqleek_s2_mdb_range_a_03(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_03(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_03 VALUES
  (1, 1, 1024, 'wide', 21474836.47),
  (2, 1, 1024 + 1, CONCAT('wide', '_range'), 21474836.47 + 1),
  (3, 2, -1, REPEAT('w', LEAST(128, 120)), (-1 * (21474836.47)));
INSERT INTO sqleek_s2_mdb_range_b_03 VALUES
  (1, 1, 'wide:left'), (2, 2, 'wide:right'), (3, 3, REPEAT('w', LEAST(128, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_03 AS a LEFT JOIN sqleek_s2_mdb_range_b_03 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -1024 AND 1024 + 10)
    OR (a.txt LIKE 'w%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_03 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
