#!/usr/bin/env python3
"""Compatibility writer for AFL fuzzer_stats in SQLeek Docker runs.

SQLRight's MySQL AFL-derived fuzzer can create queue/plot_data before its
native fuzzer_stats refresh point is reached. This monitor writes an explicitly
marked compatibility fuzzer_stats file for the current run only. If a native
non-empty fuzzer_stats appears, it stops and leaves the native file in place.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

RUNNING = True
MARKER = "generated_by=sqleek_fuzzer_stats_compat"


def _stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


@dataclass
class PlotResult:
    row: dict[str, str]
    error: str = ""


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_plot_data(path: Path) -> PlotResult:
    if not path.exists():
        return PlotResult({}, "plot_data_missing")
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError as exc:
        return PlotResult({}, f"plot_data_read_error:{exc}")
    if not lines:
        return PlotResult({}, "plot_data_empty")
    try:
        reader = csv.DictReader(lines)
        rows = [row for row in reader if row and any((value or "").strip() for value in row.values())]
    except csv.Error as exc:
        return PlotResult({}, f"plot_data_csv_error:{exc}")
    if not rows:
        return PlotResult({}, "plot_data_header_only")
    row = rows[-1]
    if _to_int(row.get("total_execs"), -1) < 0 and row.get("total_execs") not in (None, ""):
        return PlotResult(row, "plot_data_invalid_total_execs")
    return PlotResult(row, "")


def count_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    try:
        return sum(1 for child in path.iterdir() if child.is_file())
    except OSError:
        return 0


def latest_file_mtime(path: Path) -> int:
    if not path.is_dir():
        return 0
    latest = 0.0
    try:
        for child in path.iterdir():
            if child.is_file():
                try:
                    latest = max(latest, child.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return 0
    return int(latest)


def scheduler_decisions(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    pattern = re.compile(r"final=|replicated|marked low-value|injected combined")
    try:
        return sum(1 for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if pattern.search(line))
    except OSError:
        return 0


def is_native_nonempty_stats(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        return False
    return MARKER not in head


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fp:
        fp.write(text)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, path)


def build_stats(
    *,
    fuzzer_dir: Path,
    dbms: str,
    run_id: str,
    start_time: int,
    now: int,
    command_line: str,
    scheduler_log: Path,
) -> tuple[str, dict[str, str]]:
    plot = parse_plot_data(fuzzer_dir / "plot_data")
    row = plot.row
    queue_dir = fuzzer_dir / "queue"
    crashes_dir = fuzzer_dir / "crashes"
    hangs_dir = fuzzer_dir / "hangs"

    queue_total = count_files(queue_dir)
    crashes_total = count_files(crashes_dir)
    hangs_total = count_files(hangs_dir)

    if row and row.get("total_execs") not in (None, ""):
        execs_done = _to_int(row.get("total_execs"), 0)
        execs_done_source = "plot_data.total_execs"
    else:
        execs_done = queue_total
        execs_done_source = "queue_file_count_compat"

    if row and row.get("paths_total") not in (None, ""):
        paths_total = _to_int(row.get("paths_total"), queue_total)
        paths_total_source = "plot_data.paths_total"
    else:
        paths_total = queue_total
        paths_total_source = "queue_file_count"

    if row and row.get("unique_crashes") not in (None, ""):
        unique_crashes = _to_int(row.get("unique_crashes"), crashes_total)
        unique_crashes_source = "plot_data.unique_crashes"
    else:
        unique_crashes = crashes_total
        unique_crashes_source = "crashes_dir_count"

    if row and row.get("unique_hangs") not in (None, ""):
        unique_hangs = _to_int(row.get("unique_hangs"), hangs_total)
        unique_hangs_source = "plot_data.unique_hangs"
    else:
        unique_hangs = hangs_total
        unique_hangs_source = "hangs_dir_count"

    run_time = max(0, now - start_time)
    execs_per_sec = _to_float(row.get("execs_per_sec") if row else None, 0.0)
    if execs_per_sec == 0.0 and execs_done_source == "plot_data.total_execs" and run_time > 0:
        execs_per_sec = execs_done / run_time

    last_crash = latest_file_mtime(crashes_dir)
    last_hang = latest_file_mtime(hangs_dir)
    last_path = latest_file_mtime(queue_dir)

    fields: dict[str, str] = {
        "start_time": str(start_time),
        "last_update": str(now),
        "run_time": str(run_time),
        "fuzzer_pid": "0",
        "cycles_done": str(_to_int(row.get("cycles_done") if row else None, 0)),
        "execs_done": str(execs_done),
        "execs_per_sec": f"{execs_per_sec:.2f}",
        "paths_total": str(paths_total),
        "paths_favored": "0",
        "paths_found": str(paths_total),
        "paths_imported": "0",
        "max_depth": str(_to_int(row.get("max_depth") if row else None, 0)),
        "cur_path": str(_to_int(row.get("cur_path") if row else None, 0)),
        "pending_favs": str(_to_int(row.get("pending_favs") if row else None, 0)),
        "pending_total": str(_to_int(row.get("pending_total") if row else None, 0)),
        "variable_paths": "0",
        "stability": "0.00%",
        "bitmap_cvg": "0.00%",
        "unique_crashes": str(unique_crashes),
        "unique_hangs": str(unique_hangs),
        "last_path": str(last_path),
        "last_crash": str(last_crash),
        "last_hang": str(last_hang),
        "execs_since_crash": str(execs_done if unique_crashes == 0 else 0),
        "exec_timeout": "0",
        "afl_banner": f"sqleek-{dbms}",
        "afl_version": "SQLRight-AFL-compat",
        "target_mode": "sqleek_sqlright",
        "command_line": command_line,
        "slowest_exec_ms": "0",
        "peak_rss_mb": "0",
        "generated_by": "sqleek_fuzzer_stats_compat",
        "execs_done_source": execs_done_source,
        "paths_total_source": paths_total_source,
        "unique_crashes_source": unique_crashes_source,
        "unique_hangs_source": unique_hangs_source,
        "plot_data_status": plot.error or "ok",
        "queue_file_count": str(queue_total),
        "scheduler_decisions": str(scheduler_decisions(scheduler_log)),
        "run_id": run_id,
    }

    lines = [MARKER]
    order = [
        "start_time", "last_update", "run_time", "fuzzer_pid", "cycles_done",
        "execs_done", "execs_per_sec", "paths_total", "paths_favored",
        "paths_found", "paths_imported", "max_depth", "cur_path",
        "pending_favs", "pending_total", "variable_paths", "stability",
        "bitmap_cvg", "unique_crashes", "unique_hangs", "last_path",
        "last_crash", "last_hang", "execs_since_crash", "exec_timeout",
        "afl_banner", "afl_version", "target_mode", "command_line",
        "slowest_exec_ms", "peak_rss_mb", "generated_by",
        "execs_done_source", "paths_total_source", "unique_crashes_source",
        "unique_hangs_source", "plot_data_status", "queue_file_count",
        "scheduler_decisions", "run_id",
    ]
    for key in order:
        lines.append(f"{key:<20}: {fields[key]}")
    return "\n".join(lines) + "\n", fields


def write_once(
    *,
    fuzzer_dir: Path,
    dbms: str,
    run_id: str,
    start_time: int,
    now: int,
    command_line: str,
    scheduler_log: Path,
    log_file: Path | None = None,
) -> str:
    stats_path = fuzzer_dir / "fuzzer_stats"
    if is_native_nonempty_stats(stats_path):
        _log(log_file, f"native fuzzer_stats present; compat writer stopping: {stats_path}")
        return "native"
    if not fuzzer_dir.exists():
        return "waiting"
    text, fields = build_stats(
        fuzzer_dir=fuzzer_dir,
        dbms=dbms,
        run_id=run_id,
        start_time=start_time,
        now=now,
        command_line=command_line,
        scheduler_log=scheduler_log,
    )
    atomic_write(stats_path, text)
    _log(log_file, f"wrote compat fuzzer_stats execs_done={fields['execs_done']} source={fields['execs_done_source']} paths_total={fields['paths_total']} plot={fields['plot_data_status']}")
    return "compat"


def _log(log_file: Path | None, message: str) -> None:
    if not log_file:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fp:
            fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def monitor(args: argparse.Namespace) -> int:
    fuzzer_dir = Path(args.fuzzer_dir) if args.fuzzer_dir else Path(args.output_dir) / f"{args.dbms}_memory" / "default"
    scheduler_log = Path(args.scheduler_log)
    log_file = Path(args.log_file) if args.log_file else None
    start_time = int(args.start_time or time.time())
    command_line = args.command_line or "unknown"
    interval = max(1.0, float(args.interval))

    _log(log_file, f"compat monitor start fuzzer_dir={fuzzer_dir} interval={interval}s")
    status = "waiting"
    while RUNNING:
        status = write_once(
            fuzzer_dir=fuzzer_dir,
            dbms=args.dbms,
            run_id=args.run_id,
            start_time=start_time,
            now=int(time.time()),
            command_line=command_line,
            scheduler_log=scheduler_log,
            log_file=log_file,
        )
        if status == "native" or args.once:
            break
        time.sleep(interval)

    if status != "native":
        write_once(
            fuzzer_dir=fuzzer_dir,
            dbms=args.dbms,
            run_id=args.run_id,
            start_time=start_time,
            now=int(time.time()),
            command_line=command_line,
            scheduler_log=scheduler_log,
            log_file=log_file,
        )
    _log(log_file, "compat monitor stop")
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SQLeek AFL fuzzer_stats compatibility writer")
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "/workspace/output"))
    p.add_argument("--fuzzer-dir", default="")
    p.add_argument("--dbms", default=os.environ.get("DBMS", "mysql"))
    p.add_argument("--run-id", default=os.environ.get("RUN_ID", "manual"))
    p.add_argument("--interval", default=os.environ.get("FUZZER_STATS_COMPAT_INTERVAL", "5"))
    p.add_argument("--start-time", type=int, default=0)
    p.add_argument("--command-line", default=os.environ.get("SQLEEK_FUZZ_COMMAND", ""))
    p.add_argument("--scheduler-log", default=os.environ.get("SCHEDULER_LOG", os.path.join(os.environ.get("LOG_DIR", "/workspace/logs"), "scheduler.log")))
    p.add_argument("--log-file", default=os.path.join(os.environ.get("LOG_DIR", "/workspace/logs"), "fuzzer_stats_compat.log"))
    p.add_argument("--once", action="store_true")
    return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    return monitor(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
