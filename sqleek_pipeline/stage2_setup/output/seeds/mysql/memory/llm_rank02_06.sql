-- SQLeek Stage2 MySQL seed: view expansion + window Item evaluation + ALTER TABLE reprepare
DROP VIEW IF EXISTS sqleek_s2_v_06;
DROP TABLE IF EXISTS sqleek_s2_win_06;
CREATE TABLE sqleek_s2_win_06 (
  id INT PRIMARY KEY,
  grp INT,
  amount DECIMAL(40,10),
  txt VARCHAR(200),
  KEY k_grp (grp),
  KEY k_txt (txt(32))
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_win_06 VALUES
  (1, 1, 123456789.98765, 'edge'),
  (2, 1, 123456789.98765 + 1, CONCAT('edge', '_b')),
  (3, 2, -123456789.98765, REPEAT('e', 20));
CREATE VIEW sqleek_s2_v_06 AS SELECT id, grp, amount, CAST(txt AS CHAR) AS txt FROM sqleek_s2_win_06 WHERE amount IS NOT NULL;
SET @lim := 12;
PREPARE sqleek_win FROM 'SELECT id, grp, rn, prev_txt FROM (SELECT id, grp, ROW_NUMBER() OVER (PARTITION BY grp ORDER BY amount DESC, txt) AS rn, LAG(txt) OVER (PARTITION BY grp ORDER BY id) AS prev_txt, txt FROM sqleek_s2_v_06) AS d WHERE rn <= ? ORDER BY grp, rn, CAST(prev_txt AS CHAR)';
EXECUTE sqleek_win USING @lim;
ALTER TABLE sqleek_s2_win_06 MODIFY txt TEXT, ADD COLUMN marker VARCHAR(64) DEFAULT 'edge', ADD INDEX k_marker (marker);
UPDATE sqleek_s2_win_06 SET txt = CONCAT(CAST(txt AS CHAR), ':', marker), amount = amount + 123456789.98765 WHERE id <= 3;
EXECUTE sqleek_win USING @lim;
DEALLOCATE PREPARE sqleek_win;
DROP VIEW sqleek_s2_v_06;
