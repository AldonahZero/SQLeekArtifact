from __future__ import annotations

from dataclasses import dataclass

from src.core import Column, DatabaseSchema, Relation

from .byte_reader import ByteReader, InputExhausted
from .dialects import PostgreSQLDialect, SQLDialect


@dataclass(frozen=True, slots=True)
class GeneratedStatement:
    sql: str
    byte_start: int
    byte_end: int
    bytes_consumed: int


class SimpleStatementGenerator:
    """Deterministic bounded generator; every statement consumes exactly four bytes."""

    STATEMENT_WIDTH = 4
    MAX_SQL_LENGTH = 4096
    MAX_NESTING_DEPTH = 2
    OPERATIONS = (
        "create_table", "create_view", "insert", "select", "update",
        "delete", "alter_add", "drop_view", "drop_table",
    )
    TYPES = ("INTEGER", "TEXT")

    def generate(self, schema: DatabaseSchema, reader: ByteReader, dialect: SQLDialect | None = None) -> str | None:
        generated = self.generate_with_trace(schema, reader, dialect)
        return generated.sql if generated else None

    def generate_with_trace(self, schema: DatabaseSchema, reader: ByteReader,
                            dialect: SQLDialect | None = None) -> GeneratedStatement | None:
        dialect = dialect or PostgreSQLDialect()
        start = reader.offset
        try:
            opcode, selector, detail, extra = reader.take(self.STATEMENT_WIDTH)
        except InputExhausted:
            return None
        tables = tuple(relation for relation in schema.relations if relation.kind == "table")
        views = tuple(relation for relation in schema.relations if relation.kind == "view")
        operation = self.OPERATIONS[opcode % len(self.OPERATIONS)]
        if not tables:
            operation = "create_table"

        if operation == "create_table":
            name = self._unique_name("t", selector, {relation.name for relation in schema.relations})
            column = self._unique_name("c", detail, set())
            sql = f"CREATE TABLE {name}({column} {self.TYPES[extra % len(self.TYPES)]});"
        elif operation == "create_view":
            table = self._pick(tables, selector)
            name = self._unique_name("v", detail, {relation.name for relation in schema.relations})
            column = self._pick_columns(table, extra)[0]
            sql = f"CREATE VIEW {name} AS SELECT {column.name} FROM {table.name};"
        elif operation == "insert":
            table = self._pick(tables, selector)
            columns = table.columns or (Column("c_0", "integer", "INTEGER", True),)
            names = ", ".join(column.name for column in columns)
            values = ", ".join(self._literal(column, detail + index + extra, dialect) for index, column in enumerate(columns))
            sql = f"INSERT INTO {table.name}({names}) VALUES ({values});"
        elif operation == "select":
            sql = self._select(schema, tables, selector, detail, extra, dialect, depth=0)
        elif operation == "update":
            table = self._pick(tables, selector)
            column = self._pick_columns(table, detail)[0]
            sql = f"UPDATE {table.name} SET {column.name} = {self._literal(column, extra, dialect)};"
        elif operation == "delete":
            table = self._pick(tables, selector)
            sql = f"DELETE FROM {table.name};"
        elif operation == "alter_add":
            table = self._pick(tables, selector)
            name = self._unique_name("c", detail, {column.name for column in table.columns})
            sql = f"ALTER TABLE {table.name} ADD COLUMN {name} {self.TYPES[extra % len(self.TYPES)]};"
        elif operation == "drop_view":
            if views:
                sql = f"DROP VIEW {self._pick(views, selector).name};"
            else:
                table = self._pick(tables, selector)
                sql = f"SELECT {self._pick_columns(table, detail)[0].name} FROM {table.name};"
        else:
            sql = f"DROP TABLE {self._pick(tables, selector).name};"

        if len(sql) > self.MAX_SQL_LENGTH:
            raise RuntimeError(f"generated SQL exceeds {self.MAX_SQL_LENGTH} bytes")
        end = reader.offset
        consumed = end - start
        if consumed <= 0:
            raise RuntimeError("generator made no byte progress")
        return GeneratedStatement(sql, start, end, consumed)

    def _select(self, schema: DatabaseSchema, tables: tuple[Relation, ...], selector: int, detail: int,
                extra: int, dialect: SQLDialect, depth: int) -> str:
        relations = schema.relations or tables
        relation = self._pick(relations, selector)
        column = self._pick_columns(relation, detail)[0]
        projection = column.name
        source = relation.name
        predicate_column = column.name
        clauses: list[str] = []

        if extra & 0x08 and len(tables) >= 2:
            left = self._pick(tables, selector)
            right = tables[(tables.index(left) + 1) % len(tables)]
            left_column = self._pick_columns(left, detail)[0]
            right_column = self._pick_columns(right, detail)[0]
            projection = f"{left.name}.{left_column.name}"
            predicate_column = projection
            source = f"{left.name} JOIN {right.name} ON {left.name}.{left_column.name} = {right.name}.{right_column.name}"
            column = left_column
        if extra & 0x01:
            clauses.append(f"WHERE {predicate_column} = {self._literal(column, detail, dialect)}")
        if extra & 0x02:
            clauses.append(f"ORDER BY {predicate_column}")
        if extra & 0x04:
            clauses.append(dialect.limit_clause((detail % 8) + 1))
        if extra & 0x10 and depth < min(1, self.MAX_NESTING_DEPTH):
            table = self._pick(tables, selector)
            nested_column = self._pick_columns(table, detail)[0]
            projection = f"(SELECT {nested_column.name} FROM {table.name} {dialect.limit_clause(1)})"
        suffix = " " + " ".join(clauses) if clauses else ""
        return f"SELECT {projection} FROM {source}{suffix};"

    @staticmethod
    def _pick(options: tuple[Relation, ...], selector: int) -> Relation:
        return options[selector % len(options)]

    @staticmethod
    def _pick_columns(relation: Relation, selector: int) -> tuple[Column, ...]:
        if not relation.columns:
            return (Column("c_0", "integer", "INTEGER", True),)
        return (relation.columns[selector % len(relation.columns)],)

    @staticmethod
    def _unique_name(prefix: str, seed: int, existing: set[str]) -> str:
        value = seed
        for _ in range(256):
            candidate = f"{prefix}_{value}"
            if candidate not in existing:
                return candidate
            value = (value + 1) % 256
        raise RuntimeError(f"no available {prefix} identifier")

    @staticmethod
    def _literal(column: Column, value: int, dialect: SQLDialect) -> str:
        if column.normalized_type in {"integer", "decimal", "float"}:
            return str(value)
        if column.normalized_type == "boolean":
            return dialect.boolean_literal(bool(value % 2))
        return f"'s_{value}'"
