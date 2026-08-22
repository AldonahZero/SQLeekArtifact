-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_05;
CREATE TABLE sqleek_s2_sqlite_record_05(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_05(id,a,b,c,d,e) VALUES
  (1, 65535, 'range', x'abcdef', CAST(65535.00001 AS REAL), CAST('65535.00001' AS NUMERIC)),
  (2, -65535, printf('%.*c', 256, 'r'), zeroblob((256 % 257) + 1), -CAST(65535.00001 AS REAL), NULL),
  (3, 9223372036854775807, 'range_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_05_idx ON sqleek_s2_sqlite_record_05(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_05
  WHERE (b >= 'range' OR a BETWEEN -65535 AND 65535)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_05
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
