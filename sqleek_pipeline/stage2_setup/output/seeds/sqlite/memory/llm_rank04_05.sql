-- SQLeek Stage2 SQLite seed: view/subquery flattening + cast/mem copy
DROP VIEW IF EXISTS sqleek_s2_sqlite_view_05_v;
DROP TABLE IF EXISTS sqleek_s2_sqlite_view_05;
CREATE TABLE sqleek_s2_sqlite_view_05(id INTEGER PRIMARY KEY, x TEXT, y BLOB, z INTEGER);
INSERT INTO sqleek_s2_sqlite_view_05 VALUES
  (1, 'range', x'abcdef', 65535),
  (2, printf('%.*c', 256, 'r'), zeroblob((256 % 1024) + 3), -65535),
  (3, NULL, x'', 0);
CREATE VIEW sqleek_s2_sqlite_view_05_v AS
  SELECT id, coalesce(x, 'NULL') AS x2, length(y) AS ylen, CAST(z AS REAL) AS zr
  FROM sqleek_s2_sqlite_view_05;
SELECT v1.id, quote(v1.x2), v2.ylen, typeof(v1.zr), (v1.zr + v2.ylen) AS score
  FROM sqleek_s2_sqlite_view_05_v AS v1
  LEFT JOIN sqleek_s2_sqlite_view_05_v AS v2 ON v2.id <> v1.id
 WHERE (v1.x2 GLOB '*r*' OR v2.ylen IS NOT NULL)
 ORDER BY score DESC, v1.x2 COLLATE BINARY
 LIMIT 20;
DELETE FROM sqleek_s2_sqlite_view_05 WHERE id IN (SELECT id FROM sqleek_s2_sqlite_view_05_v WHERE ylen = 0 OR zr < 0);
SELECT count(*), total(ylen) FROM sqleek_s2_sqlite_view_05_v;
