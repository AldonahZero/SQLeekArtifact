-- SQLeek Stage2 MySQL seed: view expansion + window Item evaluation + ALTER TABLE reprepare
DROP VIEW IF EXISTS sqleek_s2_v_03;
DROP TABLE IF EXISTS sqleek_s2_win_03;
CREATE TABLE sqleek_s2_win_03 (
  id INT PRIMARY KEY,
  grp INT,
  amount DECIMAL(40,10),
  txt VARCHAR(200),
  KEY k_grp (grp),
  KEY k_txt (txt(32))
) ENGINE=InnoDB;
INSERT INTO sqleek_s2_win_03 VALUES
  (1, 1, 21474836.47, 'wide'),
  (2, 1, 21474836.47 + 1, CONCAT('wide', '_b')),
  (3, 2, -21474836.47, REPEAT('w', 20));
CREATE VIEW sqleek_s2_v_03 AS SELECT id, grp, amount, CAST(txt AS CHAR) AS txt FROM sqleek_s2_win_03 WHERE amount IS NOT NULL;
SET @lim := 20;
PREPARE sqleek_win FROM 'SELECT id, grp, rn, prev_txt FROM (SELECT id, grp, ROW_NUMBER() OVER (PARTITION BY grp ORDER BY amount DESC, txt) AS rn, LAG(txt) OVER (PARTITION BY grp ORDER BY id) AS prev_txt, txt FROM sqleek_s2_v_03) AS d WHERE rn <= ? ORDER BY grp, rn, CAST(prev_txt AS CHAR)';
EXECUTE sqleek_win USING @lim;
ALTER TABLE sqleek_s2_win_03 MODIFY txt BLOB, ADD COLUMN marker VARCHAR(64) DEFAULT 'wide', ADD INDEX k_marker (marker);
UPDATE sqleek_s2_win_03 SET txt = CONCAT(CAST(txt AS CHAR), ':', marker), amount = amount + 21474836.47 WHERE id <= 3;
EXECUTE sqleek_win USING @lim;
DEALLOCATE PREPARE sqleek_win;
DROP VIEW sqleek_s2_v_03;
