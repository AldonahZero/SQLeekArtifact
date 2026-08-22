-- SQLeek Stage2 MariaDB seed: filesort + aggregate cached item compare
DROP TABLE IF EXISTS sqleek_s2_mdb_aggr_01;
CREATE TABLE sqleek_s2_mdb_aggr_01(
  id INT PRIMARY KEY,
  grp INT,
  s VARCHAR(512),
  d DECIMAL(50,20),
  raw VARBINARY(2048),
  KEY k_grp_s (grp, s)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_aggr_01 VALUES
  (1, 1, 'beta', -999999.0001, REPEAT(CHAR(65), 64)),
  (2, 1, CONCAT('beta', '_dup'), (-1 * (-999999.0001)), REPEAT(CHAR(66), 64)),
  (3, 2, REPEAT('b', LEAST(64, 200)), CAST(64 AS DECIMAL(50,20)), REPEAT(CHAR(67), 64));
SELECT grp,
       CHAR_LENGTH(CAST(s AS CHAR)) AS l,
       AVG(d + (CHAR_LENGTH(s) / 10.0)) AS avgd,
       SUM(OCTET_LENGTH(raw)) AS bytes
  FROM sqleek_s2_mdb_aggr_01
 GROUP BY grp, l
 ORDER BY AVG(d + (CHAR_LENGTH(s) / 10.0)) DESC, CAST(l AS DECIMAL(20,5)) / 10
 LIMIT 10;
SELECT grp, packed
  FROM (
        SELECT grp, GROUP_CONCAT(CAST(s AS CHAR) ORDER BY s SEPARATOR '|') AS packed
          FROM sqleek_s2_mdb_aggr_01
         GROUP BY grp
       ) AS packed_rows
 ORDER BY CHAR_LENGTH(packed), packed;
