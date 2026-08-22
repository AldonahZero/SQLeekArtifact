-- SQLeek Stage2 SQLite seed: btree payload overflow + expression index
PRAGMA cache_size = -2000;
DROP TABLE IF EXISTS sqleek_s2_sqlite_btree_04;
CREATE TABLE sqleek_s2_sqlite_btree_04(
  k TEXT PRIMARY KEY,
  payload BLOB,
  note TEXT,
  v INTEGER
) WITHOUT ROWID;
INSERT INTO sqleek_s2_sqlite_btree_04(k,payload,note,v) VALUES
  ('json:0', zeroblob(192), printf('%.*c', 192 / 2, 'j'), 4096),
  ('json:1', x'012345', 'json_short', -4096),
  ('json:2', zeroblob(192 + 17), upper('json'), 4096 + 1);
CREATE INDEX sqleek_s2_sqlite_btree_04_expr ON sqleek_s2_sqlite_btree_04(substr(note,1,16), length(payload), v);
ANALYZE;
SELECT k, length(payload), substr(note,1,12)
  FROM sqleek_s2_sqlite_btree_04
  WHERE substr(note,1,1) BETWEEN 'A' AND 'z'
  ORDER BY substr(note,1,16), length(payload), v DESC;
UPDATE sqleek_s2_sqlite_btree_04
  SET note = note || ':' || CAST(v AS TEXT),
      payload = payload || x'012345'
  WHERE v IN (4096, -4096, 4096 + 1);
SELECT quote(k), length(payload), typeof(payload) FROM sqleek_s2_sqlite_btree_04 ORDER BY k DESC;
