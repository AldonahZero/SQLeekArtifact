-- SQLeek Stage2 SQLite seed: VDBE frames, triggers, FK, savepoints
PRAGMA foreign_keys = ON;
DROP TABLE IF EXISTS sqleek_s2_sqlite_log_00;
DROP TABLE IF EXISTS sqleek_s2_sqlite_child_00;
DROP TABLE IF EXISTS sqleek_s2_sqlite_parent_00;
CREATE TABLE sqleek_s2_sqlite_parent_00(id INTEGER PRIMARY KEY, tag TEXT UNIQUE, amount NUMERIC);
CREATE TABLE sqleek_s2_sqlite_child_00(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES sqleek_s2_sqlite_parent_00(id) ON UPDATE CASCADE ON DELETE CASCADE, payload TEXT);
CREATE TABLE sqleek_s2_sqlite_log_00(msg TEXT, old_id INTEGER, new_id INTEGER);
CREATE TRIGGER sqleek_s2_sqlite_child_00_ai AFTER INSERT ON sqleek_s2_sqlite_child_00 BEGIN
  INSERT INTO sqleek_s2_sqlite_log_00 VALUES ('insert:' || NEW.payload, NULL, NEW.id);
END;
INSERT INTO sqleek_s2_sqlite_parent_00 VALUES (1, 'alpha', CAST('123.456' AS NUMERIC)), (2, 'alpha_2', -CAST('123.456' AS NUMERIC));
SAVEPOINT s2_0;
INSERT INTO sqleek_s2_sqlite_child_00(id,pid,payload)
  SELECT x, CASE WHEN x % 2 = 0 THEN 2 ELSE 1 END, 'alpha:' || x
  FROM (WITH RECURSIVE r(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM r WHERE x < 6) SELECT x FROM r);
UPDATE sqleek_s2_sqlite_parent_00 SET id = id + 7 WHERE id = 2;
SELECT p.tag, c.id, c.pid, lag(c.payload) OVER (PARTITION BY c.pid ORDER BY c.id) AS prev_payload
  FROM sqleek_s2_sqlite_parent_00 AS p JOIN sqleek_s2_sqlite_child_00 AS c ON c.pid = p.id
  ORDER BY p.tag, c.id;
ROLLBACK TO s2_0;
RELEASE s2_0;
SELECT count(*), coalesce(group_concat(msg, '|'), 'empty') FROM sqleek_s2_sqlite_log_00;
