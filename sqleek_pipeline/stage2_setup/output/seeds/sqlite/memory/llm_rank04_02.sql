-- SQLeek Stage2 SQLite seed: view/subquery flattening + cast/mem copy
DROP VIEW IF EXISTS sqleek_s2_sqlite_view_02_v;
DROP TABLE IF EXISTS sqleek_s2_sqlite_view_02;
CREATE TABLE sqleek_s2_sqlite_view_02(id INTEGER PRIMARY KEY, x TEXT, y BLOB, z INTEGER);
INSERT INTO sqleek_s2_sqlite_view_02 VALUES
  (1, 'mix', x'7f0001', 255),
  (2, printf('%.*c', 96, 'm'), zeroblob((96 % 1024) + 3), -255),
  (3, NULL, x'', 0);
CREATE VIEW sqleek_s2_sqlite_view_02_v AS
  SELECT id, coalesce(x, 'NULL') AS x2, length(y) AS ylen, CAST(z AS REAL) AS zr
  FROM sqleek_s2_sqlite_view_02;
SELECT v1.id, quote(v1.x2), v2.ylen, typeof(v1.zr), (v1.zr + v2.ylen) AS score
  FROM sqleek_s2_sqlite_view_02_v AS v1
  LEFT JOIN sqleek_s2_sqlite_view_02_v AS v2 ON v2.id <> v1.id
 WHERE (v1.x2 GLOB '*m*' OR v2.ylen IS NOT NULL)
 ORDER BY score DESC, v1.x2 COLLATE BINARY
 LIMIT 20;
DELETE FROM sqleek_s2_sqlite_view_02 WHERE id IN (SELECT id FROM sqleek_s2_sqlite_view_02_v WHERE ylen = 0 OR zr < 0);
SELECT count(*), total(ylen) FROM sqleek_s2_sqlite_view_02_v;
