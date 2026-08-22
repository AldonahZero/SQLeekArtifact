from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from src.core import Column, DatabaseSchema, DBMSAdapter, ExecutionResult, ExecutionStatus, Relation


class PostgreSQLAdapter(DBMSAdapter):
    port = 35432
    _sqlstate_re = re.compile(r"ERROR:\s+([0-9A-Z]{5}):\s*(.*)")

    def __init__(self, project_root: Path | None = None, timeout_seconds: float = 10.0) -> None:
        super().__init__(project_root, timeout_seconds)
        self.install_dir = self.project_root / "install/postgresql-smoke"
        self.bin_dir = self.install_dir / "bin"
        self.runtime_dir = self.project_root / "runtime/adapter-postgresql"
        self.data_dir = self.runtime_dir / "data"
        self.socket_dir = self.runtime_dir / "socket"
        self.log_dir = self.project_root / "logs/adapter-smoke"
        self.server_log = self.log_dir / "postgresql-server.log"

    def _as_postgres(self, *args: str, timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["runuser", "-u", "postgres", "--", *args], text=True, capture_output=True,
            timeout=timeout or self.timeout_seconds, check=check,
        )

    def _psql(self, database: str, statement: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return self._as_postgres(
            str(self.bin_dir / "psql"), "-X", "-h", str(self.socket_dir), "-p", str(self.port),
            "-d", database, "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-At", "-F", "\t",
            "-c", statement, timeout=timeout,
        )

    def start(self) -> None:
        if self.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.server_log.touch(exist_ok=True)
        shutil.chown(self.server_log, user="postgres", group="postgres")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.runtime_dir, self.socket_dir):
            shutil.chown(path, user="postgres", group="postgres")
        if not (self.data_dir / "PG_VERSION").exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.chown(self.data_dir, user="postgres", group="postgres")
            result = self._as_postgres(
                str(self.bin_dir / "initdb"), "-D", str(self.data_dir), "--no-locale", "--encoding=UTF8",
                timeout=60,
            )
            if result.returncode:
                raise RuntimeError(f"PostgreSQL initdb failed: {result.stderr or result.stdout}")
        result = self._as_postgres(
            str(self.bin_dir / "pg_ctl"), "-D", str(self.data_dir), "-l", str(self.server_log),
            "-o", f"-h 127.0.0.1 -p {self.port} -k {self.socket_dir}", "-w", "start", timeout=30,
        )
        if result.returncode or not self.is_alive():
            raise RuntimeError(f"PostgreSQL start failed: {result.stderr or result.stdout}\n{self.get_server_log()}")

    def stop(self) -> None:
        if not (self.data_dir / "PG_VERSION").exists():
            return
        if self.is_alive():
            self._as_postgres(
                str(self.bin_dir / "pg_ctl"), "-D", str(self.data_dir), "-m", "fast", "-w", "stop", timeout=30,
            )

    def is_alive(self) -> bool:
        if not self.socket_dir.exists():
            return False
        result = self._as_postgres(
            str(self.bin_dir / "pg_isready"), "-h", str(self.socket_dir), "-p", str(self.port), timeout=3,
        )
        return result.returncode == 0

    def reset_database(self) -> None:
        if not self.is_alive():
            raise RuntimeError("PostgreSQL is not running")
        quoted = '"' + self.database_name.replace('"', '""') + '"'
        self._psql("postgres", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{self.database_name}' AND pid <> pg_backend_pid();")
        self._psql("postgres", f"DROP DATABASE IF EXISTS {quoted};")
        result = self._psql("postgres", f"CREATE DATABASE {quoted};")
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)

    def execute(self, statement: str) -> ExecutionResult:
        started = time.perf_counter()
        try:
            result = self._psql(self.database_name, statement)
        except subprocess.TimeoutExpired as exc:
            alive = self.is_alive()
            return ExecutionResult(ExecutionStatus.TIMEOUT, message="statement timed out", duration_ms=(time.perf_counter()-started)*1000,
                                   server_alive=alive, stdout=exc.stdout or "", stderr=exc.stderr or "")
        duration = (time.perf_counter() - started) * 1000
        alive = self.is_alive()
        if result.returncode == 0:
            return ExecutionResult(ExecutionStatus.OK, duration_ms=duration, server_alive=alive,
                                   stdout=result.stdout, stderr=result.stderr)
        match = self._sqlstate_re.search(result.stderr)
        sqlstate = match.group(1) if match else None
        message = match.group(2).strip() if match else result.stderr.strip()
        if not alive:
            status = ExecutionStatus.SERVER_CRASH
        elif sqlstate == "42601":
            status = ExecutionStatus.SYNTAX_ERROR
        elif sqlstate and (sqlstate.startswith("42") or sqlstate.startswith("22") or sqlstate.startswith("23")):
            status = ExecutionStatus.SEMANTIC_ERROR
        elif sqlstate and sqlstate.startswith("08"):
            status = ExecutionStatus.CONNECTION_LOST
        else:
            status = ExecutionStatus.OTHER_ERROR
        return ExecutionResult(status, sqlstate=sqlstate, error_code=sqlstate, message=message, duration_ms=duration,
                               server_alive=alive, stdout=result.stdout, stderr=result.stderr)

    def query_schema(self) -> DatabaseSchema:
        sql = """
SELECT c.table_name,
       CASE WHEN t.table_type = 'VIEW' THEN 'view' ELSE 'table' END,
       c.column_name, c.data_type, COALESCE(c.domain_name, c.udt_name, c.data_type), c.is_nullable
FROM information_schema.columns c
JOIN information_schema.tables t USING (table_catalog, table_schema, table_name)
WHERE c.table_catalog = current_database() AND c.table_schema = 'public'
  AND t.table_type IN ('BASE TABLE','VIEW')
ORDER BY c.table_name, c.ordinal_position;
"""
        result = self._psql(self.database_name, sql)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return self._schema_from_rows(result.stdout)

    @staticmethod
    def _normalize_type(native: str) -> str:
        mapping = {"int2": "integer", "int4": "integer", "int8": "integer", "varchar": "string", "text": "string",
                   "bool": "boolean", "float4": "float", "float8": "float", "numeric": "decimal"}
        return mapping.get(native, native)

    @classmethod
    def _schema_from_rows(cls, output: str) -> DatabaseSchema:
        grouped: dict[tuple[str, str], list[Column]] = {}
        for line in output.splitlines():
            if not line.strip():
                continue
            table, kind, column, data_type, native, nullable = line.split("\t")
            grouped.setdefault((table, kind), []).append(
                Column(column, cls._normalize_type(native), native or data_type, nullable == "YES")
            )
        return DatabaseSchema(tuple(Relation(name, kind, tuple(columns)) for (name, kind), columns in grouped.items()))

    def get_server_log(self) -> str:
        return self.server_log.read_text(errors="replace") if self.server_log.exists() else ""

    def get_connection_info(self) -> dict[str, str | int]:
        return {"host": "127.0.0.1", "port": self.port, "socket": str(self.socket_dir), "database": self.database_name,
                "user": "postgres"}
