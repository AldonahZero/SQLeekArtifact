-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_05;
CREATE TABLE sqleek_s2_sqlite_btree_05(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_05(k,payload,note,v) VALUES
  ('range:0', zeroblob(256), printf('%.*c', 256 / 2, 'r'), 65535),
  ('range:1', x'abcdef', 'range_short', -65535),
  ('range:2', zeroblob(256 + 17), upper('range'), 65535 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_05_expr ON sqleek_s2_sqlite_btree_05(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_05
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_05
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'abcdef'
  WHERE v IN (65535, -65535, 65535 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_05 ORDER BY k DESC;
