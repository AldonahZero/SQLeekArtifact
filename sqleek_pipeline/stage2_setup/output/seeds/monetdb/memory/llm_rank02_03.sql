-- SQLeek Stage2 MonetDB seed: column delta/update storage churn
-- Codex prompt targets: GDKmalloc GDKrealloc bind_col_data bind_updates update_col_execute bat_storage.c
DROP TABLE IF EXISTS sqleek_s2_monet_delta_03;
CREATE TABLE sqleek_s2_monet_delta_03 (
  id INTEGER NOT NULL,
  grp INTEGER,
  k BIGINT,
  txt VARCHAR(2048),
  decv DECIMAL(18,4),
  flag BOOLEAN,
  CONSTRAINT sqleek_s2_monet_delta_03_id_pos CHECK (id >= 0)
);
INSERT INTO sqleek_s2_monet_delta_03(id, grp, k, txt, decv, flag) VALUES
  (1, 0, 1024, 'delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_', CAST(-27182.8182 AS DECIMAL(18,4)), FALSE),
  (2, 1, -1024, 'delta_short', -CAST(-27182.8182 AS DECIMAL(18,4)), FALSE),
  (3, 1, 1024 + 17, 'delta_tail', CAST(-27182.8182 AS DECIMAL(18,4)) + 1, TRUE),
  (4, 2, 1024 * 2, 'delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_delta_delta_payload_', CAST(-27182.8182 AS DECIMAL(18,4)) * -1, FALSE);
CREATE INDEX sqleek_s2_monet_delta_03_grp_k_idx ON sqleek_s2_monet_delta_03(grp, k);
START TRANSACTION;
UPDATE sqleek_s2_monet_delta_03
   SET txt = txt || ':' || CAST(k AS VARCHAR(64)),
       k = k + CASE WHEN flag THEN 1024 ELSE -1024 END,
       decv = decv + CAST(id AS DECIMAL(18,4))
 WHERE grp IN (0, 1, 2);
DELETE FROM sqleek_s2_monet_delta_03 WHERE id = 2 AND k < 0;
INSERT INTO sqleek_s2_monet_delta_03(id, grp, k, txt, decv, flag)
  SELECT id + 100, grp + 10, k * -1, txt || ':copy', decv * -1, NOT flag
    FROM sqleek_s2_monet_delta_03
   WHERE id IN (1, 3, 4);
SELECT grp,
       COUNT(*) AS cnt,
       SUM(k) AS sum_k,
       MIN(decv) AS min_dec,
       MAX(CHAR_LENGTH(txt)) AS max_txt
  FROM sqleek_s2_monet_delta_03
 WHERE k BETWEEN -(1024 * 4) AND (1024 * 4)
 GROUP BY grp
HAVING COUNT(*) >= 1
 ORDER BY max_txt DESC, sum_k, grp;
COMMIT;
