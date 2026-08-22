-- SQLeek Stage2 SQLite seed: VDBE frames, triggers, FK, savepoints
PRAGMA foreign_keys = ON;
DROP TABLE IF EXISTS sqleek_s2_sqlite_log_07;
DROP TABLE IF EXISTS sqleek_s2_sqlite_child_07;
DROP TABLE IF EXISTS sqleek_s2_sqlite_parent_07;
CREATE TABLE sqleek_s2_sqlite_parent_07(id INTEGER PRIMARY KEY, tag TEXT UNIQUE, amount NUMERIC);
CREATE TABLE sqleek_s2_sqlite_child_07(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES sqleek_s2_sqlite_parent_07(id) ON UPDATE CASCADE ON DELETE CASCADE, payload TEXT);
CREATE TABLE sqleek_s2_sqlite_log_07(msg TEXT, old_id INTEGER, new_id INTEGER);
CREATE TRIGGER sqleek_s2_sqlite_child_07_ai AFTER INSERT ON sqleek_s2_sqlite_child_07 BEGIN
  INSERT INTO sqleek_s2_sqlite_log_07 VALUES ('insert:' || NEW.payload, NULL, NEW.id);
END;
INSERT INTO sqleek_s2_sqlite_parent_07 VALUES (1, 'nullish', CAST('0.0000001' AS NUMERIC)), (2, 'nullish_2', -CAST('0.0000001' AS NUMERIC));
SAVEPOINT s2_7;
INSERT INTO sqleek_s2_sqlite_child_07(id,pid,payload)
  SELECT x, CASE WHEN x % 2 = 0 THEN 2 ELSE 1 END, 'nullish:' || x
  FROM (WITH RECURSIVE r(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM r WHERE x < 6) SELECT x FROM r);
UPDATE sqleek_s2_sqlite_parent_07 SET id = id + 2048 WHERE id = 2;
SELECT p.tag, c.id, c.pid, lag(c.payload) OVER (PARTITION BY c.pid ORDER BY c.id) AS prev_payload
  FROM sqleek_s2_sqlite_parent_07 AS p JOIN sqleek_s2_sqlite_child_07 AS c ON c.pid = p.id
  ORDER BY p.tag, c.id;
ROLLBACK TO s2_7;
RELEASE s2_7;
SELECT count(*), coalesce(group_concat(msg, '|'), 'empty') FROM sqleek_s2_sqlite_log_07;
