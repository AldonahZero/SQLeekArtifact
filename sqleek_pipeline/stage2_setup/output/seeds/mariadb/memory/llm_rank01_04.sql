-- SQLeek Stage2 MariaDB seed: range optimizer + join buffer + type casts
DROP TABLE IF EXISTS sqleek_s2_mdb_range_b_04;
DROP TABLE IF EXISTS sqleek_s2_mdb_range_a_04;
CREATE TABLE sqleek_s2_mdb_range_a_04(
  id INT PRIMARY KEY,
  grp INT,
  v BIGINT,
  txt VARCHAR(256),
  d DECIMAL(40,20),
  KEY k_grp_v (grp, v),
  KEY k_txt (txt)
) ENGINE=InnoDB;
CREATE TABLE sqleek_s2_mdb_range_b_04(
  id INT PRIMARY KEY,
  aid INT,
  marker VARCHAR(256),
  KEY k_aid (aid),
  KEY k_marker (marker)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_range_a_04 VALUES
  (1, 1, 4096, 'json', -99999.999),
  (2, 1, 4096 + 1, CONCAT('json', '_range'), -99999.999 + 1),
  (3, 2, -1, REPEAT('j', LEAST(192, 120)), (-1 * (-99999.999)));
INSERT INTO sqleek_s2_mdb_range_b_04 VALUES
  (1, 1, 'json:left'), (2, 2, 'json:right'), (3, 3, REPEAT('j', LEAST(192, 120)));
SELECT a.id, b.marker, CAST(CONCAT(a.txt, ':', a.d) AS CHAR) AS packed
  FROM sqleek_s2_mdb_range_a_04 AS a LEFT JOIN sqleek_s2_mdb_range_b_04 AS b ON b.aid = a.id
 WHERE (a.grp = 1 AND a.v BETWEEN -4096 AND 4096 + 10)
    OR (a.txt LIKE 'j%' AND b.marker IS NOT NULL)
 ORDER BY packed DESC, a.v
 LIMIT 20;
SELECT grp, COUNT(*), SUM(CAST(d AS DECIMAL(30,10))) FROM sqleek_s2_mdb_range_a_04 GROUP BY grp HAVING COUNT(*) > 0 ORDER BY 3 DESC;
