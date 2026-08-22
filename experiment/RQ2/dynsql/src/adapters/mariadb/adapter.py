from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from src.adapters.mysql.adapter import MySQLAdapter


class MariaDBAdapter(MySQLAdapter):
    """Thin MariaDB lifecycle layer over the shared MySQL protocol/schema adapter."""

    port = 34061

    def __init__(self, project_root: Path | None = None, timeout_seconds: float = 10.0) -> None:
        super().__init__(project_root, timeout_seconds)
        self.install_dir = self.project_root / "install/mariadb-smoke"
        self.bin_dir = self.install_dir / "bin"
        self.runtime_dir = self.project_root / "runtime/adapter-mariadb"
        self.data_dir = self.runtime_dir / "data"
        self.run_dir = self.runtime_dir / "run"
        self.log_dir = self.project_root / "logs/adapter-smoke"
        self.server_log = self.log_dir / "mariadb-server.log"
        self.pid_file = self.run_dir / "mariadbd.pid"
        self.socket = self.run_dir / "mariadb.sock"

    def _client(self, statement: str, database: str | None = None,
                timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        command = [str(self.bin_dir / "mariadb"), "--no-defaults", "--protocol=socket",
                   f"--socket={self.socket}", "-uroot", "--batch", "--raw", "--skip-column-names"]
        if database:
            command.append(database)
        command.extend(["-e", statement])
        return subprocess.run(command, text=True, capture_output=True,
                              timeout=timeout or self.timeout_seconds)

    def _admin(self, *args: str, timeout: float = 3) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.bin_dir / "mariadb-admin"), "--no-defaults", "--protocol=socket",
                               f"--socket={self.socket}", "-uroot", *args], text=True,
                              capture_output=True, timeout=timeout)

    def start(self) -> None:
        if self.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.runtime_dir, self.data_dir, self.run_dir):
            path.mkdir(parents=True, exist_ok=True)
            if os.geteuid() == 0:
                shutil.chown(path, user="mysql", group="mysql")
        if not (self.data_dir / "mysql").exists():
            installer = self.bin_dir / "mariadb-install-db"
            if not installer.exists():
                installer = self.install_dir / "scripts" / "mariadb-install-db"
            result = subprocess.run(
                ["runuser", "-u", "mysql", "--", str(installer), "--no-defaults",
                 f"--basedir={self.install_dir}", f"--datadir={self.data_dir}",
                 "--auth-root-authentication-method=normal", "--skip-test-db"],
                text=True, capture_output=True, timeout=180,
            )
            if result.returncode:
                raise RuntimeError(f"MariaDB initialization failed: {result.stderr or result.stdout}")
        log_handle = self.server_log.open("a", encoding="utf-8")
        command = ["runuser", "-u", "mysql", "--", str(self.bin_dir / "mariadbd"), "--no-defaults",
                   f"--basedir={self.install_dir}", f"--datadir={self.data_dir}", f"--socket={self.socket}",
                   f"--pid-file={self.pid_file}", f"--log-error={self.server_log}", "--skip-networking"]
        self._process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT,
                                         text=True, start_new_session=True)
        log_handle.close()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.is_alive():
                return
            if self._process.poll() is not None:
                break
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"MariaDB start failed\n{self.get_server_log()[-4000:]}")

    def stop(self) -> None:
        if self.is_alive():
            try:
                self._admin("shutdown", timeout=20)
            except subprocess.TimeoutExpired:
                pass
        if self._process and self._process.poll() is None:
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self._process.pid, signal.SIGTERM)
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self._process.pid, signal.SIGKILL)
                    self._process.wait(timeout=5)
        self._process = None

    def get_connection_info(self) -> dict[str, str | int]:
        info = super().get_connection_info()
        info["dbms"] = "mariadb"
        return info
