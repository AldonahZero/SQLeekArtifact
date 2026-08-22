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

from src.adapters.mysql.adapter import MySQLAdapter
from src.scheduler import DynamicQueryScheduler


class AFLMySQLAdapter(MySQLAdapter):
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
        self.run_dir = self.runtime_dir / "run"
        self.socket_dir = self.runtime_root / "s"
        digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:16]
        self.socket = self.socket_dir / f"m{digest}.sock"
        self.pid_file = self.run_dir / "mysqld.pid"
        self.log_dir = log_root.resolve()
        self.server_log = self.log_dir / f"{run_id}.mysql.log"
        self.port = 0
        self.keep_runtime = keep_runtime

    def _as_mysql_user(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        mysql_uid = pwd.getpwnam("mysql").pw_uid
        if os.geteuid() == mysql_uid:
            command = list(args)
        else:
            command = ["runuser", "-u", "mysql", "--preserve-environment", "--", *args]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout or self.timeout_seconds,
        )

    def _chown_mysql(self, path: Path) -> None:
        if os.geteuid() == 0 and path.exists():
            shutil.chown(path, user="mysql", group="mysql")

    def start(self) -> None:
        if self.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        self.server_log.touch(exist_ok=True)
        for path in (self.runtime_root, self.runtime_dir, self.data_dir, self.run_dir, self.socket_dir, self.log_dir, self.server_log):
            self._chown_mysql(path)
        if not (self.data_dir / "mysql").exists():
            result = self._as_mysql_user(
                str(self.bin_dir / "mysqld"),
                "--no-defaults",
                "--initialize-insecure",
                f"--basedir={self.install_dir}",
                f"--datadir={self.data_dir}",
                f"--log-error={self.server_log}",
                timeout=180,
            )
            if result.returncode:
                raise RuntimeError(f"MySQL initialization failed: {result.stderr or result.stdout}\n{self.get_server_log()[-4000:]}")
        log_handle = self.server_log.open("a", encoding="utf-8")
        command = [
            "runuser", "-u", "mysql", "--preserve-environment", "--",
            str(self.bin_dir / "mysqld"),
            "--no-defaults",
            f"--basedir={self.install_dir}",
            f"--datadir={self.data_dir}",
            f"--socket={self.socket}",
            f"--pid-file={self.pid_file}",
            f"--log-error={self.server_log}",
            "--skip-networking",
            "--mysqlx=OFF",
        ]
        self._process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        log_handle.close()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.is_alive():
                return
            if self._process.poll() is not None:
                break
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"MySQL start failed\n{self.get_server_log()[-4000:]}")

    def stop(self) -> None:
        pid = self._mysqld_pid()
        try:
            if self.is_alive():
                try:
                    self._admin("shutdown", timeout=30)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if self._process and self._process.poll() is None:
                try:
                    self._process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self._process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(self._process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self._process.wait(timeout=5)
            self._terminate_own_mysqld(pid)
            self._process = None
            if not self.keep_runtime:
                if self.runtime_dir.exists():
                    shutil.rmtree(self.runtime_dir, ignore_errors=True)
                try:
                    self.socket.unlink(missing_ok=True)
                except TypeError:
                    if self.socket.exists():
                        self.socket.unlink()

    def _mysqld_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def _terminate_own_mysqld(self, pid: int | None) -> None:
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
    parser = argparse.ArgumentParser(description="Run one isolated MySQL AFL/DynSQL input")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--install-dir", type=Path, default=PROJECT_ROOT / "install/mysql-afl")
    parser.add_argument("--runtime-root", type=Path, default=PROJECT_ROOT / "runtime/mysql-afl")
    parser.add_argument("--log-root", type=Path, default=PROJECT_ROOT / "logs/mysql-afl-build/single-input")
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
    run_id = args.run_id or f"mysqlafl-{os.getpid()}-{int(time.time() * 1000)}"
    adapter = AFLMySQLAdapter(
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
        result = scheduler.run(input_path, "mysql")
    except Exception as exc:
        adapter.stop()
        payload = {"input_path": str(input_path.resolve()), "dbms": "mysql", "final_status": "HARNESS_EXCEPTION", "message": str(exc), "run_id": run_id}
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