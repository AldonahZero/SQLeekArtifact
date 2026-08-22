from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import DatabaseSchema, ExecutionResult


class DBMSAdapter(ABC):
    database_name = "dynsql_test"

    def __init__(self, project_root: Path | None = None, timeout_seconds: float = 10.0) -> None:
        self.project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> "DBMSAdapter":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_alive(self) -> bool: ...

    @abstractmethod
    def reset_database(self) -> None: ...

    @abstractmethod
    def execute(self, statement: str) -> ExecutionResult: ...

    @abstractmethod
    def query_schema(self) -> DatabaseSchema: ...

    @abstractmethod
    def get_server_log(self) -> str: ...

    @abstractmethod
    def get_connection_info(self) -> dict[str, str | int]: ...
