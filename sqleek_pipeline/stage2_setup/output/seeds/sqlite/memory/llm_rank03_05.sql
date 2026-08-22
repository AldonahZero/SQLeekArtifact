-- SQLeek Stage2 SQLite seed: temp btree, aggregate, window, compound select
DROP TABLE IF EXISTS sqleek_s2_sqlite_sort_05;
CREATE TABLE sqleek_s2_sqlite_sort_05(grp INTEGER, k TEXT, v NUMERIC, pad TEXT);
WITH RECURSIVE r(x) AS (
  VALUES(0) UNION ALL SELECT x + 1 FROM r WHERE x < 9
)
INSERT INTO sqleek_s2_sqlite_sort_05
SELECT x % 3,
       'range_' || x,
       CASE WHEN x % 2 = 0 THEN CAST('65535.00001' AS NUMERIC) + x ELSE -CAST('65535.00001' AS NUMERIC) - x END,
       printf('%.*c', (256 % 90) + x + 1, 'r')
FROM r;
CREATE INDEX sqleek_s2_sqlite_sort_05_gkv ON sqleek_s2_sqlite_sort_05(grp, v DESC, k);
SELECT grp, k, v,
       dense_rank() OVER (PARTITION BY grp ORDER BY v DESC, k) AS dr,
       sum(length(pad)) OVER (PARTITION BY grp ORDER BY k ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_len
  FROM sqleek_s2_sqlite_sort_05
 WHERE v BETWEEN -(65535 * 1000000.0) AND (65535 * 1000000.0)
 UNION ALL
SELECT grp, 'total:' || grp, sum(v), count(*), sum(length(pad))
  FROM sqleek_s2_sqlite_sort_05
 GROUP BY grp
 ORDER BY 1, 3 DESC, 2;
