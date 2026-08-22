from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExecutionStatus(StrEnum):
    OK = "OK"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    SEMANTIC_ERROR = "SEMANTIC_ERROR"
    OTHER_ERROR = "OTHER_ERROR"
    TIMEOUT = "TIMEOUT"
    CONNECTION_LOST = "CONNECTION_LOST"
    SERVER_CRASH = "SERVER_CRASH"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    sqlstate: str | None = None
    error_code: str | None = None
    message: str = ""
    duration_ms: float = 0.0
    server_alive: bool = False
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    normalized_type: str
    native_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class Relation:
    name: str
    kind: str
    columns: tuple[Column, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in {"table", "view"}:
            raise ValueError(f"unsupported relation kind: {self.kind}")


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    relations: tuple[Relation, ...] = field(default_factory=tuple)

    def relation(self, name: str) -> Relation | None:
        return next((relation for relation in self.relations if relation.name == name), None)
