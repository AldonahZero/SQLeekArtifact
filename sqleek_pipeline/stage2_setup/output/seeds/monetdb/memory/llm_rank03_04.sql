-- SQLeek Stage2 MonetDB seed: WITH/set operations/check insert expression cleanup
-- Codex prompt targets: rel_with_query sql_insert_check rel_value_exp rel_binop_ freeMalBlk
DROP TABLE IF EXISTS sqleek_s2_monet_with_04;
CREATE TABLE sqleek_s2_monet_with_04 (
  id INTEGER NOT NULL,
  tag VARCHAR(256),
  amount DECIMAL(20,5),
  CONSTRAINT sqleek_s2_monet_with_04_amount_ck CHECK (amount > CAST(-1000000000 AS DECIMAL(20,5)))
);
WITH input_rows(id, tag, amount) AS (
  SELECT 1, 'wide', CAST(65535.0001 AS DECIMAL(20,5))
  UNION ALL
  SELECT 2, 'wide_u', CAST(4096 AS DECIMAL(20,5))
  UNION ALL
  SELECT 3, 'wide_v', -CAST(65535.0001 AS DECIMAL(20,5))
)
INSERT INTO sqleek_s2_monet_with_04
SELECT id, tag, amount
  FROM input_rows
 WHERE amount IS NOT NULL;
WITH lhs(id, tag, amount) AS (
  SELECT id, tag, amount FROM sqleek_s2_monet_with_04 WHERE id IN (1, 2, 3)
),
rhs(id, tag, amount) AS (
  SELECT 2, 'wide_u', CAST(4096 AS DECIMAL(20,5))
  UNION ALL
  SELECT 4, 'wide_new', CAST(4096 + 4 AS DECIMAL(20,5))
)
SELECT id, tag, amount FROM lhs
UNION ALL
SELECT id, tag, amount FROM rhs
EXCEPT
SELECT id, tag, amount FROM lhs WHERE amount < 0
ORDER BY id, tag;
SELECT id, tag
  FROM sqleek_s2_monet_with_04
INTERSECT
SELECT id, tag
  FROM sqleek_s2_monet_with_04
 WHERE amount >= 0
 ORDER BY id;
