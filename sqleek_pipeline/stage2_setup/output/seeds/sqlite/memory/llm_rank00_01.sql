-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_01;
CREATE TABLE sqleek_s2_sqlite_record_01(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_01(id,a,b,c,d,e) VALUES
  (1, 64, 'beta', x'00ff7f', CAST(-999999.0001 AS REAL), CAST('-999999.0001' AS NUMERIC)),
  (2, -64, printf('%.*c', 64, 'b'), zeroblob((64 % 257) + 1), -CAST(-999999.0001 AS REAL), NULL),
  (3, 9223372036854775807, 'beta_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_01_idx ON sqleek_s2_sqlite_record_01(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_01
  WHERE (b >= 'beta' OR a BETWEEN -64 AND 64)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_01
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
