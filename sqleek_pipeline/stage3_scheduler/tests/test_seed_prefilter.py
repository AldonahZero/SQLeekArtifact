import tempfile
import unittest
from pathlib import Path

import seed_scheduler as scheduler


class SeedPrefilterTests(unittest.TestCase):
    def test_recursive_sql_and_test_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "a.sql").write_text("SELECT 1;\n", encoding="utf-8")
            (root / "nested" / "b.test").write_text("SELECT 2;\n", encoding="utf-8")
            (root / "id:000001,orig:seed.sql.0").write_text("SELECT 4;\n", encoding="utf-8")
            (root / "nested" / "c.txt").write_text("SELECT 3;\n", encoding="utf-8")
            names = [p.relative_to(root).as_posix() for p in scheduler.iter_seed_candidates(root)]
            self.assertEqual(names, ["a.sql", "id:000001,orig:seed.sql.0", "nested/b.test"])

    def test_mysqltest_directives_are_deferred(self):
        cases = [
            b"--source include/have_innodb.inc\nSELECT 1;\n",
            b"--error ER_PARSE_ERROR\nSELECT bad;\n",
            b"--let $x=1\nSELECT 1;\n",
            b"connect (con1,localhost,root,,test);\n",
        ]
        for data in cases:
            status, reason = scheduler.classify_mysql_seed_bytes(data, ".test")
            self.assertEqual(status, "deferred")
            self.assertIn("mysqltest_directive", reason)

    def test_plain_test_file_is_kept(self):
        status, reason = scheduler.classify_mysql_seed_bytes(b"SELECT * FROM t;\n", ".test")
        self.assertEqual(status, "kept")
        self.assertEqual(reason, "")

    def test_empty_is_rejected_and_nul_separator_is_kept(self):
        self.assertEqual(scheduler.classify_mysql_seed_bytes(b"", ".sql")[0], "rejected")
        status, reason = scheduler.classify_mysql_seed_bytes(b"SELECT 1;\x00SELECT 2;", ".test")
        self.assertEqual((status, reason), ("kept", ""))

    def test_prefilter_keeps_recursive_compatible_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seeds"
            keep = root / "keep"
            deferred = root / "deferred"
            log_file = root / "scheduler.log"
            seed_dir.mkdir()
            (seed_dir / "sub").mkdir()
            (seed_dir / "sub" / "plain.test").write_text("SELECT 1;\n", encoding="utf-8")
            (seed_dir / "directive.test").write_text("--source include/foo.inc\n", encoding="utf-8")
            old_log = scheduler.SCHEDULER_LOG
            scheduler.SCHEDULER_LOG = log_file
            try:
                kept = scheduler.run_prefilter("mysql", seed_dir, keep, deferred)
            finally:
                scheduler.SCHEDULER_LOG = old_log
            self.assertEqual(kept, 1)
            self.assertEqual(len(list(keep.glob("*.sql"))), 1)
            self.assertEqual(len(list(deferred.glob("*.sql"))), 1)
            self.assertIn("seeds_in=2 kept=1 deferred=1 rejected=0", log_file.read_text(encoding="utf-8"))

    def test_prefilter_defers_sqlright_oversize_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seeds"
            keep = root / "keep"
            deferred = root / "deferred"
            log_file = root / "scheduler.log"
            seed_dir.mkdir()
            (seed_dir / "small.sql").write_text("SELECT 1;\n", encoding="utf-8")
            (seed_dir / "large.sql").write_bytes(b"S" * (scheduler.SQLRIGHT_MAX_FILE_BYTES + 1))
            old_log = scheduler.SCHEDULER_LOG
            scheduler.SCHEDULER_LOG = log_file
            try:
                kept = scheduler.run_prefilter("mysql", seed_dir, keep, deferred)
            finally:
                scheduler.SCHEDULER_LOG = old_log
            self.assertEqual(kept, 1)
            self.assertEqual(len(list(keep.glob("*.sql"))), 1)
            self.assertEqual(len(list(deferred.glob("*.sql"))), 1)
            self.assertIn("sqlright_max_file_exceeded", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
