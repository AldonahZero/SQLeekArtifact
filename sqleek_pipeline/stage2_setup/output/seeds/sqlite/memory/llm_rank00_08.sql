-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_08;
CREATE TABLE sqleek_s2_sqlite_record_08(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_08(id,a,b,c,d,e) VALUES
  (1, 8192, 'sort', x'123456', CAST(314159.26535 AS REAL), CAST('314159.26535' AS NUMERIC)),
  (2, -8192, printf('%.*c', 768, 's'), zeroblob((768 % 257) + 1), -CAST(314159.26535 AS REAL), NULL),
  (3, 9223372036854775807, 'sort_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_08_idx ON sqleek_s2_sqlite_record_08(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_08
  WHERE (b >= 'sort' OR a BETWEEN -8192 AND 8192)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_08
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
