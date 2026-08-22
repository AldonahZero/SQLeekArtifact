-- SQLeek Stage2 MariaDB seed: filesort + aggregate cached item compare
DROP TABLE IF EXISTS sqleek_s2_mdb_aggr_00;
CREATE TABLE sqleek_s2_mdb_aggr_00(
  id INT PRIMARY KEY,
  grp INT,
  s VARCHAR(512),
  d DECIMAL(50,20),
  raw VARBINARY(2048),
  KEY k_grp_s (grp, s)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_aggr_00 VALUES
  (1, 1, 'alpha', 123.456, REPEAT(CHAR(65), 32)),
  (2, 1, CONCAT('alpha', '_dup'), (-1 * (123.456)), REPEAT(CHAR(66), 32)),
  (3, 2, REPEAT('a', LEAST(32, 200)), CAST(7 AS DECIMAL(50,20)), REPEAT(CHAR(67), 32));
SELECT grp,
       CHAR_LENGTH(CAST(s AS CHAR)) AS l,
       AVG(d + (CHAR_LENGTH(s) / 10.0)) AS avgd,
       SUM(OCTET_LENGTH(raw)) AS bytes
  FROM sqleek_s2_mdb_aggr_00
 GROUP BY grp, l
 ORDER BY AVG(d + (CHAR_LENGTH(s) / 10.0)) DESC, CAST(l AS DECIMAL(20,5)) / 10
 LIMIT 10;
SELECT grp, packed
  FROM (
        SELECT grp, GROUP_CONCAT(CAST(s AS CHAR) ORDER BY s SEPARATOR '|') AS packed
          FROM sqleek_s2_mdb_aggr_00
         GROUP BY grp
       ) AS packed_rows
 ORDER BY CHAR_LENGTH(packed), packed;
