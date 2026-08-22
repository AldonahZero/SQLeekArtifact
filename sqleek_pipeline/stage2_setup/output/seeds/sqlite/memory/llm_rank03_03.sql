-- SQLeek Stage2 SQLite seed: temp btree, aggregate, window, compound select
DROP TABLE IF EXISTS sqleek_s2_sqlite_sort_03;
CREATE TABLE sqleek_s2_sqlite_sort_03(grp INTEGER, k TEXT, v NUMERIC, pad TEXT);
WITH RECURSIVE r(x) AS (
  VALUES(0) UNION ALL SELECT x + 1 FROM r WHERE x < 9
)
INSERT INTO sqleek_s2_sqlite_sort_03
SELECT x % 3,
       'wide_' || x,
       CASE WHEN x % 2 = 0 THEN CAST('21474836.47' AS NUMERIC) + x ELSE -CAST('21474836.47' AS NUMERIC) - x END,
       printf('%.*c', (128 % 90) + x + 1, 'w')
FROM r;
CREATE INDEX sqleek_s2_sqlite_sort_03_gkv ON sqleek_s2_sqlite_sort_03(grp, v DESC, k);
SELECT grp, k, v,
       dense_rank() OVER (PARTITION BY grp ORDER BY v DESC, k) AS dr,
       sum(length(pad)) OVER (PARTITION BY grp ORDER BY k ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_len
  FROM sqleek_s2_sqlite_sort_03
 WHERE v BETWEEN -(1024 * 1000000.0) AND (1024 * 1000000.0)
 UNION ALL
SELECT grp, 'total:' || grp, sum(v), count(*), sum(length(pad))
  FROM sqleek_s2_sqlite_sort_03
 GROUP BY grp
 ORDER BY 1, 3 DESC, 2;
