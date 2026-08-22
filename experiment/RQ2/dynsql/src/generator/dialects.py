from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SQLDialect:
    name: str

    def boolean_literal(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def limit_clause(self, count: int) -> str:
        return f"LIMIT {count}"


class PostgreSQLDialect(SQLDialect):
    def __init__(self) -> None:
        super().__init__("postgresql")


class MySQLDialect(SQLDialect):
    def __init__(self) -> None:
        super().__init__("mysql")

    def boolean_literal(self, value: bool) -> str:
        return "1" if value else "0"


def dialect_for(name: str) -> SQLDialect:
    if name == "postgresql":
        return PostgreSQLDialect()
    if name == "mysql":
        return MySQLDialect()
    raise ValueError(f"unsupported SQL dialect: {name}")
