-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_00;
CREATE TABLE sqleek_s2_sqlite_record_00(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_00(id,a,b,c,d,e) VALUES
  (1, 7, 'alpha', x'414243', CAST(123.456 AS REAL), CAST('123.456' AS NUMERIC)),
  (2, -7, printf('%.*c', 32, 'a'), zeroblob((32 % 257) + 1), -CAST(123.456 AS REAL), NULL),
  (3, 9223372036854775807, 'alpha_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_00_idx ON sqleek_s2_sqlite_record_00(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_00
  WHERE (b >= 'alpha' OR a BETWEEN -7 AND 7)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_00
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
