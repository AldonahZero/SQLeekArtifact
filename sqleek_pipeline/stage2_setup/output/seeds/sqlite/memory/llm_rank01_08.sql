-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_08;
CREATE TABLE sqleek_s2_sqlite_btree_08(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_08(k,payload,note,v) VALUES
  ('sort:0', zeroblob(768), printf('%.*c', 768 / 2, 's'), 8192),
  ('sort:1', x'123456', 'sort_short', -8192),
  ('sort:2', zeroblob(768 + 17), upper('sort'), 8192 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_08_expr ON sqleek_s2_sqlite_btree_08(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_08
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_08
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'123456'
  WHERE v IN (8192, -8192, 8192 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_08 ORDER BY k DESC;
