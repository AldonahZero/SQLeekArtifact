-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_02;
CREATE TABLE sqleek_s2_sqlite_btree_02(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_02(k,payload,note,v) VALUES
  ('mix:0', zeroblob(96), printf('%.*c', 96 / 2, 'm'), 255),
  ('mix:1', x'7f0001', 'mix_short', -255),
  ('mix:2', zeroblob(96 + 17), upper('mix'), 255 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_02_expr ON sqleek_s2_sqlite_btree_02(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_02
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_02
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'7f0001'
  WHERE v IN (255, -255, 255 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_02 ORDER BY k DESC;
