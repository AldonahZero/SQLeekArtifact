-- SQLeek Stage2 MonetDB seed: wide string heap, window sort, BAT copy
-- Codex prompt targets: GDKrealloc GDKstrdup BATwcopy subrel_project memmove batstr.c algebra.c
DROP TABLE IF EXISTS sqleek_s2_monet_str_00;
CREATE TABLE sqleek_s2_monet_str_00 (
  id INTEGER,
  bucket INTEGER,
  label VARCHAR(512),
  payload VARCHAR(4096),
  metric DOUBLE
);
INSERT INTO sqleek_s2_monet_str_00 VALUES
  (1, 0, 'alpha', 'alpha_payload_alpha_payload_alpha_payload_', CAST(123.4567 AS DOUBLE)),
  (2, 0, 'alpha_b', 'alpha_payload_alpha_payload_', CAST(7 AS DOUBLE)),
  (3, 1, 'alpha_c', 'alpha_payload_alpha_payload_alpha_payload_alpha_payload_', CAST(-7 AS DOUBLE)),
  (4, 1, 'alpha_d', 'alpha_payload_', CAST(123.4567 AS DOUBLE) * -1),
  (5, 2, 'alpha_e', 'alpha', 0.0);
UPDATE sqleek_s2_monet_str_00
   SET payload = payload || ':' || label || ':' || CAST(id AS VARCHAR(32)),
       metric = metric + CHAR_LENGTH(payload)
 WHERE id IN (1, 2, 3, 4, 5);
SELECT bucket,
       label,
       CHAR_LENGTH(payload) AS payload_len,
       ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY CHAR_LENGTH(payload) DESC, id) AS rn,
       SUM(CHAR_LENGTH(payload)) OVER (PARTITION BY bucket ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_len
  FROM sqleek_s2_monet_str_00
 WHERE payload LIKE '%' || 'alpha' || '%'
 ORDER BY bucket, rn, payload_len DESC;
SELECT bucket,
       COUNT(*) AS cnt,
       AVG(metric) AS avg_metric,
       MAX(SUBSTRING(payload FROM 1 FOR 32)) AS sample_prefix
  FROM sqleek_s2_monet_str_00
 GROUP BY bucket
 ORDER BY cnt DESC, avg_metric;
