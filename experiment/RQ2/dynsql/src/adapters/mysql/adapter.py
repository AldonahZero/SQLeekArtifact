from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from src.core import Column, DatabaseSchema, DBMSAdapter, ExecutionResult, ExecutionStatus, Relation


class MySQLAdapter(DBMSAdapter):
    port = 34060
    _error_re = re.compile(r"ERROR\s+(\d+)\s+\(([0-9A-Z]{5})\)(?: at line \d+)?:\s*(.*)")

    def __init__(self, project_root: Path | None = None, timeout_seconds: float = 10.0) -> None:
        super().__init__(project_root, timeout_seconds)
        self.install_dir = self.project_root / "install/mysql-smoke"
        self.bin_dir = self.install_dir / "bin"
        self.runtime_dir = self.project_root / "runtime/adapter-mysql"
        self.data_dir = self.runtime_dir / "data"
        self.run_dir = self.runtime_dir / "run"
        self.log_dir = self.project_root / "logs/adapter-smoke"
        self.server_log = self.log_dir / "mysql-server.log"
        self.pid_file = self.run_dir / "mysqld.pid"
        self.socket = self.run_dir / "mysql.sock"
        self._process: subprocess.Popen[str] | None = None

    def _client(self, statement: str, database: str | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        command = [str(self.bin_dir / "mysql"), "--no-defaults", "--protocol=socket", f"--socket={self.socket}",
                   "-uroot", "--batch", "--raw", "--skip-column-names"]
        if database:
            command.append(database)
        command.extend(["-e", statement])
        return subprocess.run(command, text=True, capture_output=True, timeout=timeout or self.timeout_seconds)

    def _admin(self, *args: str, timeout: float = 3) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.bin_dir / "mysqladmin"), "--no-defaults", "--protocol=socket",
                               f"--socket={self.socket}", "-uroot", *args], text=True, capture_output=True, timeout=timeout)

    def start(self) -> None:
        if self.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.runtime_dir, self.data_dir, self.run_dir):
            path.mkdir(parents=True, exist_ok=True)
            shutil.chown(path, user="mysql", group="mysql")
        if not (self.data_dir / "mysql").exists():
            result = subprocess.run(
                ["runuser", "-u", "mysql", "--", str(self.bin_dir / "mysqld"), "--no-defaults", "--initialize-insecure",
                 f"--basedir={self.install_dir}", f"--datadir={self.data_dir}"], text=True, capture_output=True, timeout=120,
            )
            if result.returncode:
                raise RuntimeError(f"MySQL initialization failed: {result.stderr or result.stdout}")
        log_handle = self.server_log.open("a", encoding="utf-8")
        command = ["runuser", "-u", "mysql", "--", str(self.bin_dir / "mysqld"), "--no-defaults",
                   f"--basedir={self.install_dir}", f"--datadir={self.data_dir}", f"--socket={self.socket}",
                   f"--port={self.port}", f"--pid-file={self.pid_file}", "--bind-address=127.0.0.1", "--mysqlx=OFF"]
        self._process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        log_handle.close()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.is_alive():
                return
            if self._process.poll() is not None:
                break
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"MySQL start failed\n{self.get_server_log()[-4000:]}")

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

    def is_alive(self) -> bool:
        if not self.socket.exists():
            return False
        try:
            return self._admin("ping").returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def reset_database(self) -> None:
        if not self.is_alive():
            raise RuntimeError("MySQL is not running")
        name = "`" + self.database_name.replace("`", "``") + "`"
        result = self._client(f"DROP DATABASE IF EXISTS {name}; CREATE DATABASE {name};")
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)

    def execute(self, statement: str) -> ExecutionResult:
        started = time.perf_counter()
        try:
            result = self._client(statement, self.database_name)
        except subprocess.TimeoutExpired as exc:
            alive = self.is_alive()
            return ExecutionResult(ExecutionStatus.TIMEOUT, message="statement timed out", duration_ms=(time.perf_counter()-started)*1000,
                                   server_alive=alive, stdout=exc.stdout or "", stderr=exc.stderr or "")
        duration = (time.perf_counter() - started) * 1000
        alive = self.is_alive()
        if result.returncode == 0:
            return ExecutionResult(ExecutionStatus.OK, duration_ms=duration, server_alive=alive,
                                   stdout=result.stdout, stderr=result.stderr)
        match = self._error_re.search(result.stderr)
        code, sqlstate, message = (match.group(1), match.group(2), match.group(3).strip()) if match else (None, None, result.stderr.strip())
        if not alive:
            status = ExecutionStatus.SERVER_CRASH
        elif code == "1064":
            status = ExecutionStatus.SYNTAX_ERROR
        elif sqlstate and (sqlstate.startswith("42") or sqlstate.startswith("22") or sqlstate.startswith("23")):
            status = ExecutionStatus.SEMANTIC_ERROR
        elif sqlstate and sqlstate.startswith("08"):
            status = ExecutionStatus.CONNECTION_LOST
        else:
            status = ExecutionStatus.OTHER_ERROR
        return ExecutionResult(status, sqlstate=sqlstate, error_code=code, message=message, duration_ms=duration,
                               server_alive=alive, stdout=result.stdout, stderr=result.stderr)

    def query_schema(self) -> DatabaseSchema:
        sql = f"""
SELECT c.table_name,
       CASE WHEN t.table_type = 'VIEW' THEN 'view' ELSE 'table' END,
       c.column_name, c.data_type, c.column_type, c.is_nullable
FROM information_schema.tables t
JOIN information_schema.columns c ON c.table_schema=t.table_schema AND c.table_name=t.table_name
WHERE t.table_schema='{self.database_name}'
  AND t.table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
  AND t.table_type IN ('BASE TABLE','VIEW')
ORDER BY t.table_name,c.ordinal_position;
"""
        result = self._client(sql)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return self._schema_from_rows(result.stdout)

    @staticmethod
    def _normalize_type(native: str) -> str:
        base = native.lower().split("(", 1)[0]
        if base in {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}:
            return "integer"
        if base in {"char", "varchar", "text", "tinytext", "mediumtext", "longtext"}:
            return "string"
        if base in {"float", "double", "real"}:
            return "float"
        if base in {"decimal", "numeric"}:
            return "decimal"
        return base

    @classmethod
    def _schema_from_rows(cls, output: str) -> DatabaseSchema:
        grouped: dict[tuple[str, str], list[Column]] = {}
        for line in output.splitlines():
            if not line.strip():
                continue
            table, kind, column, data_type, native, nullable = line.split("\t")
            grouped.setdefault((table, kind), []).append(Column(column, cls._normalize_type(native), native or data_type, nullable == "YES"))
        return DatabaseSchema(tuple(Relation(name, kind, tuple(columns)) for (name, kind), columns in grouped.items()))

    def get_server_log(self) -> str:
        return self.server_log.read_text(errors="replace") if self.server_log.exists() else ""

    def get_connection_info(self) -> dict[str, str | int]:
        return {"host": "127.0.0.1", "port": self.port, "socket": str(self.socket), "database": self.database_name,
                "user": "root"}
