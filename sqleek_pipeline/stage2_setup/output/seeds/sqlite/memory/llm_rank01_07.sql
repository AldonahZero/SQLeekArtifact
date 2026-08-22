-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_07;
CREATE TABLE sqleek_s2_sqlite_btree_07(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_07(k,payload,note,v) VALUES
  ('nullish:0', zeroblob(512), printf('%.*c', 512 / 2, 'n'), 2048),
  ('nullish:1', x'000000', 'nullish_short', -2048),
  ('nullish:2', zeroblob(512 + 17), upper('nullish'), 2048 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_07_expr ON sqleek_s2_sqlite_btree_07(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_07
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_07
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'000000'
  WHERE v IN (2048, -2048, 2048 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_07 ORDER BY k DESC;
