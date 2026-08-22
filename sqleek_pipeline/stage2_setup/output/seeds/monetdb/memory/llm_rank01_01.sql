-- SQLeek Stage2 MonetDB seed: projection aliases, view expansion, rename DDL
-- Codex prompt targets: rel_rename subrel_project rel2bin_project exp_bin memcpy
DROP VIEW IF EXISTS sqleek_s2_monet_proj_01_v;
DROP TABLE IF EXISTS sqleek_s2_monet_proj_01;
CREATE TABLE sqleek_s2_monet_proj_01 (
  id INTEGER,
  label VARCHAR(512),
  amount DECIMAL(20,5),
  score DOUBLE
);
INSERT INTO sqleek_s2_monet_proj_01 VALUES
  (1, 'beta', CAST(-9999.1250 AS DECIMAL(20,5)), CAST(64 AS DOUBLE)),
  (2, 'beta_payload_beta_beta_payload_beta_', -CAST(-9999.1250 AS DECIMAL(20,5)), CAST(-64 AS DOUBLE)),
  (3, 'beta_alias', CAST(-9999.1250 AS DECIMAL(20,5)) + 3, CAST(64 + 3 AS DOUBLE));
CREATE VIEW sqleek_s2_monet_proj_01_v AS
  SELECT id AS rid,
         label AS base_label,
         CAST(amount AS DOUBLE) + score AS combined_score
    FROM sqleek_s2_monet_proj_01
   WHERE id >= 1;
WITH renamed_cte(alias_id, alias_label, alias_score) AS (
  SELECT rid,
         base_label || ':' || CAST(rid AS VARCHAR(32)),
         combined_score
    FROM sqleek_s2_monet_proj_01_v
)
SELECT q.alias_id AS projected_id,
       q.alias_label AS projected_label,
       q.alias_score AS projected_score
  FROM (
        SELECT alias_id, alias_label, alias_score
          FROM renamed_cte
         WHERE alias_score IS NOT NULL
       ) AS q
 ORDER BY projected_label, projected_score DESC;
ALTER TABLE sqleek_s2_monet_proj_01 ADD COLUMN extra_label VARCHAR(256);
UPDATE sqleek_s2_monet_proj_01
   SET extra_label = label || ':extra:' || CAST(id AS VARCHAR(32)),
       amount = amount + CAST(id AS DECIMAL(20,5));
DROP VIEW IF EXISTS sqleek_s2_monet_proj_01_v;
ALTER TABLE sqleek_s2_monet_proj_01 RENAME COLUMN label TO label_renamed;
SELECT id AS renamed_id,
       label_renamed AS renamed_label,
       extra_label AS copied_label,
       CAST(amount AS DOUBLE) + score AS final_score
  FROM sqleek_s2_monet_proj_01
 ORDER BY renamed_label, final_score DESC;
