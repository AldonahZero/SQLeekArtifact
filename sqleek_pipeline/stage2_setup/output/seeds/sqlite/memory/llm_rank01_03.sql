-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_03;
CREATE TABLE sqleek_s2_sqlite_btree_03(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_03(k,payload,note,v) VALUES
  ('wide:0', zeroblob(128), printf('%.*c', 128 / 2, 'w'), 1024),
  ('wide:1', x'deadbeef', 'wide_short', -1024),
  ('wide:2', zeroblob(128 + 17), upper('wide'), 1024 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_03_expr ON sqleek_s2_sqlite_btree_03(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_03
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_03
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'deadbeef'
  WHERE v IN (1024, -1024, 1024 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_03 ORDER BY k DESC;
