-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_09;
CREATE TABLE sqleek_s2_sqlite_btree_09(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_09(k,payload,note,v) VALUES
  ('cast:0', zeroblob(1024), printf('%.*c', 1024 / 2, 'c'), 32768),
  ('cast:1', x'654321', 'cast_short', -32768),
  ('cast:2', zeroblob(1024 + 17), upper('cast'), 32768 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_09_expr ON sqleek_s2_sqlite_btree_09(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_09
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_09
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'654321'
  WHERE v IN (32768, -32768, 32768 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_09 ORDER BY k DESC;
