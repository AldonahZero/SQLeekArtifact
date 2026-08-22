CREATE TABLE t3(a INT);
INSERT INTO t3 VALUES (1), (2), (3);
SELECT count(*), sum(a) FROM t3;
