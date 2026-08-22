-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_07;
CREATE TABLE sqleek_s2_sqlite_record_07(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_07(id,a,b,c,d,e) VALUES
  (1, 2048, 'nullish', x'000000', CAST(0.0000001 AS REAL), CAST('0.0000001' AS NUMERIC)),
  (2, -2048, printf('%.*c', 512, 'n'), zeroblob((512 % 257) + 1), -CAST(0.0000001 AS REAL), NULL),
  (3, 9223372036854775807, 'nullish_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_07_idx ON sqleek_s2_sqlite_record_07(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_07
  WHERE (b >= 'nullish' OR a BETWEEN -2048 AND 2048)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_07
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
