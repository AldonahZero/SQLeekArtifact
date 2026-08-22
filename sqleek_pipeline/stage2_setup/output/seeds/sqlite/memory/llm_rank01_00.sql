-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_00;
CREATE TABLE sqleek_s2_sqlite_btree_00(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_00(k,payload,note,v) VALUES
  ('alpha:0', zeroblob(32), printf('%.*c', 32 / 2, 'a'), 7),
  ('alpha:1', x'414243', 'alpha_short', -7),
  ('alpha:2', zeroblob(32 + 17), upper('alpha'), 7 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_00_expr ON sqleek_s2_sqlite_btree_00(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_00
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_00
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'414243'
  WHERE v IN (7, -7, 7 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_00 ORDER BY k DESC;
