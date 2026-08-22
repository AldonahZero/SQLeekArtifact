-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_04;
CREATE TABLE sqleek_s2_sqlite_record_04(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_04(id,a,b,c,d,e) VALUES
  (1, 4096, 'json', x'012345', CAST(-99999.999 AS REAL), CAST('-99999.999' AS NUMERIC)),
  (2, -4096, printf('%.*c', 192, 'j'), zeroblob((192 % 257) + 1), -CAST(-99999.999 AS REAL), NULL),
  (3, 9223372036854775807, 'json_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_04_idx ON sqleek_s2_sqlite_record_04(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_04
  WHERE (b >= 'json' OR a BETWEEN -4096 AND 4096)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_04
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
