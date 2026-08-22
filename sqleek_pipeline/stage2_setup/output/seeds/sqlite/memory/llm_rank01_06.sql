-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_06;
CREATE TABLE sqleek_s2_sqlite_btree_06(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_06(k,payload,note,v) VALUES
  ('edge:0', zeroblob(384), printf('%.*c', 384 / 2, 'e'), 100000),
  ('edge:1', x'bead01', 'edge_short', -100000),
  ('edge:2', zeroblob(384 + 17), upper('edge'), 100000 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_06_expr ON sqleek_s2_sqlite_btree_06(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_06
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_06
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'bead01'
  WHERE v IN (100000, -100000, 100000 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_06 ORDER BY k DESC;
