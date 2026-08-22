-- SQLeek Stage2 MariaDB seed: GIS values through group/order/type conversion
DROP TABLE IF EXISTS sqleek_s2_mdb_gis_08;
CREATE TABLE sqleek_s2_mdb_gis_08(
  id INT PRIMARY KEY,
  label VARCHAR(80),
  g GEOMETRY NOT NULL,
  d DECIMAL(40,10),
  KEY k_label (label)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_gis_08(id,label,g,d) VALUES
  (1, 'sort', ST_GeomFromText('POINT(0 0)'), 314159.26535),
  (2, 'sort_far', ST_GeomFromText('POINT(45 0)'), (-1 * (314159.26535))),
  (3, 'sort_poly', ST_GeomFromText('POLYGON((0 0,0 5,5 5,5 0,0 0))'), 8192);
SELECT label,
       ST_AsText(g) AS wkt,
       CHAR_LENGTH(ST_AsText(g)) AS wkt_len,
       CAST(d AS DECIMAL(30,5)) AS dd
  FROM sqleek_s2_mdb_gis_08
 GROUP BY label, wkt, wkt_len, dd
 ORDER BY wkt_len DESC, dd
 LIMIT 10;
SELECT MOD(id, 2) AS bucket,
       AVG(CAST(d AS DECIMAL(30,5))) AS avgd,
       GROUP_CONCAT(ST_AsText(g) ORDER BY label SEPARATOR '|') AS wkts
  FROM sqleek_s2_mdb_gis_08
 GROUP BY bucket
 ORDER BY avgd DESC;
