-- SQLeek Stage2 SQLite seed: VDBE frames, triggers, FK, savepoints
PRAGMA foreign_keys = ON;
DROP TABLE IF EXISTS sqleek_s2_sqlite_log_05;
DROP TABLE IF EXISTS sqleek_s2_sqlite_child_05;
DROP TABLE IF EXISTS sqleek_s2_sqlite_parent_05;
CREATE TABLE sqleek_s2_sqlite_parent_05(id INTEGER PRIMARY KEY, tag TEXT UNIQUE, amount NUMERIC);
CREATE TABLE sqleek_s2_sqlite_child_05(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES sqleek_s2_sqlite_parent_05(id) ON UPDATE CASCADE ON DELETE CASCADE, payload TEXT);
CREATE TABLE sqleek_s2_sqlite_log_05(msg TEXT, old_id INTEGER, new_id INTEGER);
CREATE TRIGGER sqleek_s2_sqlite_child_05_ai AFTER INSERT ON sqleek_s2_sqlite_child_05 BEGIN
  INSERT INTO sqleek_s2_sqlite_log_05 VALUES ('insert:' || NEW.payload, NULL, NEW.id);
END;
INSERT INTO sqleek_s2_sqlite_parent_05 VALUES (1, 'range', CAST('65535.00001' AS NUMERIC)), (2, 'range_2', -CAST('65535.00001' AS NUMERIC));
SAVEPOINT s2_5;
INSERT INTO sqleek_s2_sqlite_child_05(id,pid,payload)
  SELECT x, CASE WHEN x % 2 = 0 THEN 2 ELSE 1 END, 'range:' || x
  FROM (WITH RECURSIVE r(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM r WHERE x < 6) SELECT x FROM r);
UPDATE sqleek_s2_sqlite_parent_05 SET id = id + 65535 WHERE id = 2;
SELECT p.tag, c.id, c.pid, lag(c.payload) OVER (PARTITION BY c.pid ORDER BY c.id) AS prev_payload
  FROM sqleek_s2_sqlite_parent_05 AS p JOIN sqleek_s2_sqlite_child_05 AS c ON c.pid = p.id
  ORDER BY p.tag, c.id;
ROLLBACK TO s2_5;
RELEASE s2_5;
SELECT count(*), coalesce(group_concat(msg, '|'), 'empty') FROM sqleek_s2_sqlite_log_05;
