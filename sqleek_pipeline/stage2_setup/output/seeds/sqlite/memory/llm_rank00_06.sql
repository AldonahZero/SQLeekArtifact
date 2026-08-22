-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_06;
CREATE TABLE sqleek_s2_sqlite_record_06(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_06(id,a,b,c,d,e) VALUES
  (1, 100000, 'edge', x'bead01', CAST(123456789.98765 AS REAL), CAST('123456789.98765' AS NUMERIC)),
  (2, -100000, printf('%.*c', 384, 'e'), zeroblob((384 % 257) + 1), -CAST(123456789.98765 AS REAL), NULL),
  (3, 9223372036854775807, 'edge_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_06_idx ON sqleek_s2_sqlite_record_06(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_06
  WHERE (b >= 'edge' OR a BETWEEN -100000 AND 100000)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_06
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
