-- SQLeek Stage2 MySQL seed: view expansion + window Item evaluation + ALTER TABLE reprepare
DROP VIEW IF EXISTS sqleek_s2_v_00;
DROP TABLE IF EXISTS sqleek_s2_win_00;
CREATE TABLE sqleek_s2_win_00 (
  id INT PRIMARY KEY,
  grp INT,
  amount DECIMAL(40,10),
  txt VARCHAR(200),
  KEY k_grp (grp),
  KEY k_txt (txt(32))
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_win_00 VALUES
  (1, 1, 123.456, 'alpha'),
  (2, 1, 123.456 + 1, CONCAT('alpha', '_b')),
  (3, 2, -123.456, REPEAT('a', 20));
CREATE VIEW sqleek_s2_v_00 AS SELECT id, grp, amount, CAST(txt AS CHAR) AS txt FROM sqleek_s2_win_00 WHERE amount IS NOT NULL;
SET @lim := 10;
PREPARE sqleek_win FROM 'SELECT id, grp, rn, prev_txt FROM (SELECT id, grp, ROW_NUMBER() OVER (PARTITION BY grp ORDER BY amount DESC, txt) AS rn, LAG(txt) OVER (PARTITION BY grp ORDER BY id) AS prev_txt, txt FROM sqleek_s2_v_00) AS d WHERE rn <= ? ORDER BY grp, rn, CAST(prev_txt AS CHAR)';
EXECUTE sqleek_win USING @lim;
ALTER TABLE sqleek_s2_win_00 MODIFY txt TEXT, ADD COLUMN marker VARCHAR(64) DEFAULT 'alpha', ADD INDEX k_marker (marker);
UPDATE sqleek_s2_win_00 SET txt = CONCAT(CAST(txt AS CHAR), ':', marker), amount = amount + 123.456 WHERE id <= 3;
EXECUTE sqleek_win USING @lim;
DEALLOCATE PREPARE sqleek_win;
DROP VIEW sqleek_s2_v_00;
