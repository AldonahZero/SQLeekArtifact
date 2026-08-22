-- SQLeek Stage2 SQLite seed: view/subquery flattening + cast/mem copy
DROP VIEW IF EXISTS sqleek_s2_sqlite_view_03_v;
DROP TABLE IF EXISTS sqleek_s2_sqlite_view_03;
CREATE TABLE sqleek_s2_sqlite_view_03(id INTEGER PRIMARY KEY, x TEXT, y BLOB, z INTEGER);
INSERT INTO sqleek_s2_sqlite_view_03 VALUES
  (1, 'wide', x'deadbeef', 1024),
  (2, printf('%.*c', 128, 'w'), zeroblob((128 % 1024) + 3), -1024),
  (3, NULL, x'', 0);
CREATE VIEW sqleek_s2_sqlite_view_03_v AS
  SELECT id, coalesce(x, 'NULL') AS x2, length(y) AS ylen, CAST(z AS REAL) AS zr
  FROM sqleek_s2_sqlite_view_03;
SELECT v1.id, quote(v1.x2), v2.ylen, typeof(v1.zr), (v1.zr + v2.ylen) AS score
  FROM sqleek_s2_sqlite_view_03_v AS v1
  LEFT JOIN sqleek_s2_sqlite_view_03_v AS v2 ON v2.id <> v1.id
 WHERE (v1.x2 GLOB '*w*' OR v2.ylen IS NOT NULL)
 ORDER BY score DESC, v1.x2 COLLATE BINARY
 LIMIT 20;
DELETE FROM sqleek_s2_sqlite_view_03 WHERE id IN (SELECT id FROM sqleek_s2_sqlite_view_03_v WHERE ylen = 0 OR zr < 0);
SELECT count(*), total(ylen) FROM sqleek_s2_sqlite_view_03_v;
