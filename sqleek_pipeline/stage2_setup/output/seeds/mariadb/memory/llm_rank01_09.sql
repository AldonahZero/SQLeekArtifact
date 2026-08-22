-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_09;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_09;
CREATE TABLE sqleek_s2_mdb_range_a_09(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_09(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_09 VALUES
  (1, 1, 32768, 'cast', -314159.26535),
  (2, 1, 32768 + 1, CONCAT('cast', '_range'), -314159.26535 + 1),
  (3, 2, -1, REPEAT('c', LEAST(1024, 120)), (-1 * (-314159.26535)));
INSERT INTO sqleek_s2_mdb_range_b_09 VALUES
  (1, 1, 'cast:left'), (2, 2, 'cast:right'), (3, 3, REPEAT('c', LEAST(1024, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_09 AS a LEFT JOIN sqleek_s2_mdb_range_b_09 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -32768 AND 32768 + 10)
    OR (a.txt LIKE 'c%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_09 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
