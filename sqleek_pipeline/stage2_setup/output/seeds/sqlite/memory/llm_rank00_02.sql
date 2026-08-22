-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_02;
CREATE TABLE sqleek_s2_sqlite_record_02(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_02(id,a,b,c,d,e) VALUES
  (1, 255, 'mix', x'7f0001', CAST(-42.125 AS REAL), CAST('-42.125' AS NUMERIC)),
  (2, -255, printf('%.*c', 96, 'm'), zeroblob((96 % 257) + 1), -CAST(-42.125 AS REAL), NULL),
  (3, 9223372036854775807, 'mix_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_02_idx ON sqleek_s2_sqlite_record_02(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_02
  WHERE (b >= 'mix' OR a BETWEEN -255 AND 255)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_02
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
