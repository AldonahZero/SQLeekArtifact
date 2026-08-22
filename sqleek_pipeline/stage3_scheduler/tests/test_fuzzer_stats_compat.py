#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "common"))

import fuzzer_stats_compat as compat


HEADER = "unix_time,cycles_done,cur_path,paths_total,pending_total,pending_favs,map_size,unique_crashes,unique_hangs,max_depth,execs_per_sec,total_execs"


class FuzzerStatsCompatTests(unittest.TestCase):
    def make_tree(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        fuzzer = root / "out" / "mysql_memory" / "default"
        (fuzzer / "queue").mkdir(parents=True)
        (fuzzer / "crashes").mkdir()
        (fuzzer / "hangs").mkdir()
        sched = root / "logs" / "scheduler.log"
        sched.parent.mkdir()
        return tmp, root, fuzzer, sched

    def write_seed_files(self, path, count):
        path.mkdir(parents=True, exist_ok=True)
        for idx in range(count):
            (path / f"id:{idx:06d}").write_text("SELECT 1;\n", encoding="utf-8")

    def test_parse_plot_data_normal(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            (fuzzer / "plot_data").write_text(HEADER + "\n100,2,1,7,3,2,99,1,4,5,12.5,123\n", encoding="utf-8")
            result = compat.parse_plot_data(fuzzer / "plot_data")
            self.assertEqual(result.error, "")
            self.assertEqual(result.row["total_execs"], "123")
            text, fields = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=10, now=20, command_line="cmd", scheduler_log=sched)
            self.assertIn("generated_by=sqleek_fuzzer_stats_compat", text)
            self.assertEqual(fields["execs_done"], "123")
            self.assertEqual(fields["execs_done_source"], "plot_data.total_execs")

    def test_plot_data_header_only(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            self.write_seed_files(fuzzer / "queue", 3)
            (fuzzer / "plot_data").write_text(HEADER + "\n", encoding="utf-8")
            text, fields = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=10, now=20, command_line="cmd", scheduler_log=sched)
            self.assertEqual(fields["plot_data_status"], "plot_data_header_only")
            self.assertEqual(fields["execs_done"], "3")
            self.assertEqual(fields["execs_done_source"], "queue_file_count_compat")

    def test_queue_count(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            self.write_seed_files(fuzzer / "queue", 5)
            self.assertEqual(compat.count_files(fuzzer / "queue"), 5)
            _, fields = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=1, now=2, command_line="cmd", scheduler_log=sched)
            self.assertEqual(fields["paths_total"], "5")

    def test_crashes_hangs_count(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            self.write_seed_files(fuzzer / "crashes", 2)
            self.write_seed_files(fuzzer / "hangs", 4)
            _, fields = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=1, now=2, command_line="cmd", scheduler_log=sched)
            self.assertEqual(fields["unique_crashes"], "2")
            self.assertEqual(fields["unique_hangs"], "4")

    def test_atomic_write(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            target = fuzzer / "fuzzer_stats"
            compat.atomic_write(target, "abc\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "abc\n")
            self.assertEqual(list(fuzzer.glob(".fuzzer_stats.tmp.*")), [])

    def test_native_stats_not_overwritten(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            target = fuzzer / "fuzzer_stats"
            target.write_text("start_time        : 1\n", encoding="utf-8")
            status = compat.write_once(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=1, now=2, command_line="cmd", scheduler_log=sched)
            self.assertEqual(status, "native")
            self.assertEqual(target.read_text(encoding="utf-8"), "start_time        : 1\n")

    def test_stable_same_inputs(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            self.write_seed_files(fuzzer / "queue", 2)
            (fuzzer / "plot_data").write_text(HEADER + "\n100,0,0,2,0,0,0,0,0,0,0,2\n", encoding="utf-8")
            a, _ = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=100, now=130, command_line="cmd", scheduler_log=sched)
            b, _ = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=100, now=130, command_line="cmd", scheduler_log=sched)
            self.assertEqual(a, b)

    def test_corrupt_plot_data_does_not_abort(self):
        tmp, root, fuzzer, sched = self.make_tree()
        with tmp:
            self.write_seed_files(fuzzer / "queue", 1)
            (fuzzer / "plot_data").write_text(HEADER + "\n100,0,0,1,0,0,0,0,0,0,0,not_a_number\n", encoding="utf-8")
            text, fields = compat.build_stats(fuzzer_dir=fuzzer, dbms="mysql", run_id="r", start_time=1, now=2, command_line="cmd", scheduler_log=sched)
            self.assertIn("generated_by=sqleek_fuzzer_stats_compat", text)
            self.assertEqual(fields["plot_data_status"], "plot_data_invalid_total_execs")


if __name__ == "__main__":
    unittest.main()
