-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_01;
CREATE TABLE sqleek_s2_sqlite_btree_01(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_01(k,payload,note,v) VALUES
  ('beta:0', zeroblob(64), printf('%.*c', 64 / 2, 'b'), 64),
  ('beta:1', x'00ff7f', 'beta_short', -64),
  ('beta:2', zeroblob(64 + 17), upper('beta'), 64 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_01_expr ON sqleek_s2_sqlite_btree_01(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_01
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_01
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'00ff7f'
  WHERE v IN (64, -64, 64 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_01 ORDER BY k DESC;
