-- SQLeek Stage2 MariaDB seed: GIS values through group/order/type conversion
DROP TABLE IF EXISTS sqleek_s2_mdb_gis_02;
CREATE TABLE sqleek_s2_mdb_gis_02(
  id INT PRIMARY KEY,
  label VARCHAR(80),
  g GEOMETRY NOT NULL,
  d DECIMAL(40,10),
  KEY k_label (label)
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_mdb_gis_02(id,label,g,d) VALUES
  (1, 'mix', ST_GeomFromText('POINT(0 0)'), -42.125),
  (2, 'mix_far', ST_GeomFromText('POINT(45 0)'), (-1 * (-42.125))),
  (3, 'mix_poly', ST_GeomFromText('POLYGON((0 0,0 5,5 5,5 0,0 0))'), 255);
SELECT label,
       ST_AsText(g) AS wkt,
       CHAR_LENGTH(ST_AsText(g)) AS wkt_len,
       CAST(d AS DECIMAL(30,5)) AS dd
  FROM sqleek_s2_mdb_gis_02
 GROUP BY label, wkt, wkt_len, dd
 ORDER BY wkt_len DESC, dd
 LIMIT 10;
SELECT MOD(id, 2) AS bucket,
       AVG(CAST(d AS DECIMAL(30,5))) AS avgd,
       GROUP_CONCAT(ST_AsText(g) ORDER BY label SEPARATOR '|') AS wkts
  FROM sqleek_s2_mdb_gis_02
 GROUP BY bucket
 ORDER BY avgd DESC;
