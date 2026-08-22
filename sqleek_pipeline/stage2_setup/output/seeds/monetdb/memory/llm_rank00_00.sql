-- SQLeek Stage2 MonetDB seed: groupjoin, semijoin, antijoin, reorder
-- Codex prompt targets: rel2bin_groupjoin rel2bin_antijoin subrel_project sql_reorder GDKmalloc
DROP TABLE IF EXISTS sqleek_s2_monet_fact_00;
DROP TABLE IF EXISTS sqleek_s2_monet_dim_00;
CREATE TABLE sqleek_s2_monet_dim_00 (
  id INTEGER NOT NULL,
  name VARCHAR(256),
  bucket INTEGER
);
CREATE TABLE sqleek_s2_monet_fact_00 (
  id INTEGER NOT NULL,
  dim_id INTEGER,
  measure DECIMAL(20,5),
  payload VARCHAR(1024)
);
INSERT INTO sqleek_s2_monet_dim_00 VALUES
  (1, 'alpha', 0),
  (2, 'alpha_two', 1),
  (3, 'alpha_three', 1),
  (4, 'alpha_orphan', 2);
INSERT INTO sqleek_s2_monet_fact_00 VALUES
  (10, 1, CAST(123.4567 AS DECIMAL(20,5)), 'alpha_payload_'),
  (11, 1, CAST(123.4567 AS DECIMAL(20,5)) + 11, 'alpha_f11'),
  (12, 2, -CAST(123.4567 AS DECIMAL(20,5)), 'alpha_f12'),
  (13, 3, CAST(7 AS DECIMAL(20,5)), 'alpha_f13'),
  (14, 99, CAST(-7 AS DECIMAL(20,5)), 'alpha_f14');
CREATE INDEX sqleek_s2_monet_dim_00_bucket_idx ON sqleek_s2_monet_dim_00(bucket);
CREATE INDEX sqleek_s2_monet_fact_00_dim_idx ON sqleek_s2_monet_fact_00(dim_id);
SELECT d.bucket,
       d.name,
       COUNT(f.id) AS fact_count,
       SUM(f.measure) AS total_measure,
       MAX(CHAR_LENGTH(f.payload)) AS max_payload
  FROM sqleek_s2_monet_dim_00 AS d
  JOIN sqleek_s2_monet_fact_00 AS f ON f.dim_id = d.id
 WHERE EXISTS (
       SELECT 1 FROM sqleek_s2_monet_fact_00 AS fx
        WHERE fx.dim_id = d.id AND fx.measure >= f.measure
 )
 GROUP BY d.bucket, d.name
HAVING COUNT(f.id) >= 1
 ORDER BY total_measure DESC, d.name;
SELECT f.id,
       f.dim_id,
       f.measure,
       CASE WHEN f.dim_id IN (SELECT id FROM sqleek_s2_monet_dim_00 WHERE bucket IN (0, 1))
            THEN 'matched' ELSE 'candidate' END AS match_state
  FROM sqleek_s2_monet_fact_00 AS f
 WHERE NOT EXISTS (SELECT 1 FROM sqleek_s2_monet_dim_00 AS d WHERE d.id = f.dim_id AND d.bucket = 2)
 ORDER BY match_state, f.id;
