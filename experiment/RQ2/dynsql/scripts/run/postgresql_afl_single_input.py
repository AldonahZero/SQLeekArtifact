#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
import enum

if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        pass
    enum.StrEnum = StrEnum


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.postgresql.adapter import PostgreSQLAdapter
from src.scheduler import DynamicQueryScheduler


class AFLPostgreSQLAdapter(PostgreSQLAdapter):
    def __init__(
        self,
        project_root: Path,
        install_dir: Path,
        runtime_root: Path,
        log_root: Path,
        run_id: str,
        timeout_seconds: float,
        keep_runtime: bool = False,
    ) -> None:
        super().__init__(project_root, timeout_seconds)
        self.install_dir = install_dir.resolve()
        self.bin_dir = self.install_dir / "bin"
        self.runtime_root = runtime_root.resolve()
        self.runtime_dir = self.runtime_root / "work" / run_id
        self.data_dir = self.runtime_dir / "data"
        self.socket_dir = self.runtime_root / "s" / hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:12]
        self.log_dir = log_root.resolve()
        self.server_log = self.log_dir / f"{run_id}.postgresql.log"
        self.port = int(os.environ.get("DYNSQL_POSTGRESQL_AFL_PORT", "35432"))
        self.keep_runtime = keep_runtime

    def _as_postgres(self, *args: str, timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        postgres_uid = pwd.getpwnam("postgres").pw_uid
        if os.geteuid() == postgres_uid:
            command = list(args)
        else:
            command = ["runuser", "-u", "postgres", "--preserve-environment", "--", *args]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout or self.timeout_seconds,
            check=check,
        )

    def _chown_postgres(self, path: Path) -> None:
        if os.geteuid() == 0:
            shutil.chown(path, user="postgres", group="postgres")

    def start(self) -> None:
        if self.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.socket_dir.parent.mkdir(parents=True, exist_ok=True)
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        self.server_log.touch(exist_ok=True)
        for path in (self.runtime_root, self.runtime_root / "work", self.runtime_root / "s", self.runtime_dir, self.socket_dir, self.log_dir, self.server_log):
            if path.exists():
                self._chown_postgres(path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._chown_postgres(self.data_dir)
        result = self._as_postgres(
            str(self.bin_dir / "initdb"), "-D", str(self.data_dir), "--no-locale", "--encoding=UTF8", timeout=60,
        )
        if result.returncode:
            raise RuntimeError(f"PostgreSQL initdb failed: {result.stderr or result.stdout}")
        start_options = (
            f"-h '' -p {self.port} -k {self.socket_dir} "
            "-c fsync=off -c synchronous_commit=off -c full_page_writes=off "
            "-c shared_buffers=32MB -c max_connections=20"
        )
        result = self._as_postgres(
            str(self.bin_dir / "pg_ctl"), "-D", str(self.data_dir), "-l", str(self.server_log),
            "-o", start_options, "-w", "start", timeout=45,
        )
        if result.returncode or not self.is_alive():
            raise RuntimeError(f"PostgreSQL start failed: {result.stderr or result.stdout}\n{self.get_server_log()}")

    def stop(self) -> None:
        pid = self._postmaster_pid()
        try:
            if (self.data_dir / "PG_VERSION").exists() and self.is_alive():
                self._as_postgres(str(self.bin_dir / "pg_ctl"), "-D", str(self.data_dir), "-m", "fast", "-w", "stop", timeout=30)
        finally:
            self._terminate_own_postmaster(pid)
            if not self.keep_runtime:
                if self.runtime_dir.exists():
                    shutil.rmtree(self.runtime_dir, ignore_errors=True)
                if self.socket_dir.exists():
                    shutil.rmtree(self.socket_dir, ignore_errors=True)

    def _postmaster_pid(self) -> int | None:
        pid_file = self.data_dir / "postmaster.pid"
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text(encoding="utf-8", errors="replace").splitlines()[0])
        except (ValueError, IndexError):
            return None

    def _terminate_own_postmaster(self, pid: int | None) -> None:
        if not pid:
            return
        proc_cmdline = Path(f"/proc/{pid}/cmdline")
        if not proc_cmdline.exists():
            return
        cmdline = proc_cmdline.read_text(encoding="utf-8", errors="replace").replace("\x00", " ")
        if str(self.data_dir) not in cmdline:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 10
        while time.time() < deadline and proc_cmdline.exists():
            time.sleep(0.1)
        if proc_cmdline.exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one isolated PostgreSQL AFL/DynSQL input")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--install-dir", type=Path, default=PROJECT_ROOT / "install/postgresql-afl")
    parser.add_argument("--runtime-root", type=Path, default=PROJECT_ROOT / "runtime/postgresql-afl")
    parser.add_argument("--log-root", type=Path, default=PROJECT_ROOT / "logs/postgresql-afl-build/single-input")
    parser.add_argument("--max-statements", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--keep-runtime", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    os.environ.setdefault("AFL_IGNORE_PROBLEMS", "1")
    args = parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    run_id = args.run_id or f"pgafl-{os.getpid()}-{int(time.time() * 1000)}"
    adapter = AFLPostgreSQLAdapter(
        PROJECT_ROOT,
        args.install_dir if args.install_dir.is_absolute() else PROJECT_ROOT / args.install_dir,
        args.runtime_root if args.runtime_root.is_absolute() else PROJECT_ROOT / args.runtime_root,
        args.log_root if args.log_root.is_absolute() else PROJECT_ROOT / args.log_root,
        run_id,
        args.timeout_seconds,
        args.keep_runtime,
    )
    scheduler = DynamicQueryScheduler(adapter, max_statements=args.max_statements)
    try:
        result = scheduler.run(input_path, "postgresql")
    except Exception as exc:
        adapter.stop()
        payload = {"input_path": str(input_path.resolve()), "dbms": "postgresql", "final_status": "HARNESS_EXCEPTION", "message": str(exc), "run_id": run_id}
        if args.output:
            output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    payload = result.to_dict()
    payload["run_id"] = run_id
    if args.output:
        output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(payload, indent=2))
    if payload.get("crash_candidate") or payload.get("abnormal_candidate"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
