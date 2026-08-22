-- SQLeek Stage2 SQLite seed: record decode + composite index scan
DROP TABLE IF EXISTS sqleek_s2_sqlite_record_03;
CREATE TABLE sqleek_s2_sqlite_record_03(
  id INTEGER PRIMARY KEY,
  a INTEGER,
  b TEXT COLLATE NOCASE,
  c BLOB,
  d REAL,
  e NUMERIC
);
INSERT INTO sqleek_s2_sqlite_record_03(id,a,b,c,d,e) VALUES
  (1, 1024, 'wide', x'deadbeef', CAST(21474836.47 AS REAL), CAST('21474836.47' AS NUMERIC)),
  (2, -1024, printf('%.*c', 128, 'w'), zeroblob((128 % 257) + 1), -CAST(21474836.47 AS REAL), NULL),
  (3, 9223372036854775807, 'wide_tail', x'00', 0.0, '0000000000000000000000001');
CREATE INDEX sqleek_s2_sqlite_record_03_idx ON sqleek_s2_sqlite_record_03(b COLLATE NOCASE, a DESC, e);
SELECT id, typeof(e), length(c), hex(substr(c,1,8)), quote(b)
  FROM sqleek_s2_sqlite_record_03
  WHERE (b >= 'wide' OR a BETWEEN -1024 AND 1024)
  ORDER BY b COLLATE NOCASE, a DESC, e;
SELECT b, count(*), sum(length(c)), max(CAST(e AS REAL))
  FROM sqleek_s2_sqlite_record_03
  GROUP BY b COLLATE NOCASE
  HAVING count(*) >= 1
  ORDER BY 2 DESC, 1;
