-- SQLeek Stage2 MonetDB seed: WITH/set operations/check insert expression cleanup
-- Codex prompt targets: rel_with_query sql_insert_check rel_value_exp rel_binop_ freeMalBlk
DROP TABLE IF EXISTS sqleek_s2_monet_with_06;
CREATE TABLE sqleek_s2_monet_with_06 (
  id INTEGER NOT NULL,
  tag VARCHAR(256),
  amount DECIMAL(20,5),
  CONSTRAINT sqleek_s2_monet_with_06_amount_ck CHECK (amount > CAST(-1000000000 AS DECIMAL(20,5)))
);
WITH input_rows(id, tag, amount) AS (
  SELECT 1, 'edge', CAST(-214748.3648 AS DECIMAL(20,5))
  UNION ALL
  SELECT 2, 'edge_u', CAST(32768 AS DECIMAL(20,5))
  UNION ALL
  SELECT 3, 'edge_v', -CAST(-214748.3648 AS DECIMAL(20,5))
)
INSERT INTO sqleek_s2_monet_with_06
SELECT id, tag, amount
  FROM input_rows
 WHERE amount IS NOT NULL;
WITH lhs(id, tag, amount) AS (
  SELECT id, tag, amount FROM sqleek_s2_monet_with_06 WHERE id IN (1, 2, 3)
),
rhs(id, tag, amount) AS (
  SELECT 2, 'edge_u', CAST(32768 AS DECIMAL(20,5))
  UNION ALL
  SELECT 4, 'edge_new', CAST(32768 + 4 AS DECIMAL(20,5))
)
SELECT id, tag, amount FROM lhs
UNION ALL
SELECT id, tag, amount FROM rhs
EXCEPT
SELECT id, tag, amount FROM lhs WHERE amount < 0
ORDER BY id, tag;
SELECT id, tag
  FROM sqleek_s2_monet_with_06
INTERSECT
SELECT id, tag
  FROM sqleek_s2_monet_with_06
 WHERE amount >= 0
 ORDER BY id;
