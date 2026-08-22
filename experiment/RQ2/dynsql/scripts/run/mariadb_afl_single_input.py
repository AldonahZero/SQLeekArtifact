#!/usr/bin/env python3
from __future__ import annotations

import argparse
import enum
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        pass
    enum.StrEnum = StrEnum

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.mariadb import MariaDBAdapter
from src.scheduler import DynamicQueryScheduler


class IsolatedMariaDBAdapter(MariaDBAdapter):
    def __init__(self, install_dir: Path, runtime_root: Path, log_root: Path,
                 run_id: str, timeout_seconds: float, keep_runtime: bool) -> None:
        super().__init__(PROJECT_ROOT, timeout_seconds)
        self.install_dir = install_dir.resolve()
        self.bin_dir = self.install_dir / "bin"
        self.runtime_root = runtime_root.resolve()
        self.runtime_dir = self.runtime_root / "work" / run_id
        self.data_dir = self.runtime_dir / "data"
        self.run_dir = self.runtime_dir / "run"
        self.socket_dir = self.runtime_root / "s"
        digest = hashlib.sha1(run_id.encode()).hexdigest()[:16]
        self.socket = self.socket_dir / f"m{digest}.sock"
        self.pid_file = self.run_dir / "mariadbd.pid"
        self.log_dir = log_root.resolve()
        self.server_log = self.log_dir / f"{run_id}.mariadb.log"
        self.keep_runtime = keep_runtime

    def start(self) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.server_log.touch(exist_ok=True)
        if os.geteuid() == 0:
            for path in (self.runtime_root, self.socket_dir, self.log_dir, self.server_log):
                shutil.chown(path, user="mysql", group="mysql")
        super().start()

    def stop(self) -> None:
        pid = None
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip().splitlines()[0])
            except (ValueError, IndexError):
                pass
        super().stop()
        if pid:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text(errors="replace").replace("\0", " ")
                if str(self.data_dir) in cmdline:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        if not self.keep_runtime:
            shutil.rmtree(self.runtime_dir, ignore_errors=True)
            self.socket.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one isolated MariaDB DynSQL input")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--install-dir", type=Path, default=PROJECT_ROOT / "install/mariadb-smoke")
    parser.add_argument("--runtime-root", type=Path, default=PROJECT_ROOT / "runtime/mariadb-smoke")
    parser.add_argument("--log-root", type=Path, default=PROJECT_ROOT / "logs/mariadb-smoke/single-input")
    parser.add_argument("--max-statements", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--run-id")
    parser.add_argument("--keep-runtime", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    resolve = lambda p: p if p.is_absolute() else PROJECT_ROOT / p
    run_id = args.run_id or f"mariadb-{os.getpid()}-{int(time.time() * 1000)}"
    adapter = IsolatedMariaDBAdapter(resolve(args.install_dir), resolve(args.runtime_root),
                                     resolve(args.log_root), run_id, args.timeout_seconds,
                                     args.keep_runtime)
    scheduler = DynamicQueryScheduler(adapter, max_statements=args.max_statements)
    try:
        result = scheduler.run(input_path, "mysql")
        payload = result.to_dict()
        payload["dbms"] = "mariadb"
        payload["run_id"] = run_id
    except Exception as exc:
        adapter.stop()
        payload = {"input_path": str(input_path.resolve()), "dbms": "mariadb",
                   "final_status": "HARNESS_EXCEPTION", "message": str(exc), "run_id": run_id}
    if args.output:
        output_path = resolve(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n")
    if not args.quiet:
        print(json.dumps(payload, indent=2))
    return 2 if payload.get("final_status") == "HARNESS_EXCEPTION" or payload.get("crash_candidate") or payload.get("abnormal_candidate") else 0


if __name__ == "__main__":
    raise SystemExit(main())
