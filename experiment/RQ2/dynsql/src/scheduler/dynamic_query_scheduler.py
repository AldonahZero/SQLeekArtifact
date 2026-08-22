from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from src.core import DatabaseSchema, DBMSAdapter, ExecutionResult, ExecutionStatus
from src.generator import ByteReader, SimpleStatementGenerator, dialect_for


@dataclass(frozen=True, slots=True)
class StatementTrace:
    sql: str
    byte_start: int
    byte_end: int
    bytes_consumed: int


@dataclass(frozen=True, slots=True)
class QueryRunResult:
    input_path: str
    dbms: str
    statements: tuple[str, ...]
    statement_traces: tuple[StatementTrace, ...]
    execution_results: tuple[ExecutionResult, ...]
    final_schema: DatabaseSchema
    final_status: str
    duration_ms: float
    server_alive: bool
    bytes_consumed: int
    statement_count: int
    valid_statement_count: int
    ordinary_error: bool
    abnormal_candidate: bool
    crash_candidate: bool
    seed_eligible: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DynamicQueryScheduler:
    ORDINARY_ERRORS = {ExecutionStatus.SYNTAX_ERROR, ExecutionStatus.SEMANTIC_ERROR}
    SEVERE_ERRORS = {ExecutionStatus.TIMEOUT, ExecutionStatus.CONNECTION_LOST, ExecutionStatus.SERVER_CRASH}

    def __init__(self, adapter: DBMSAdapter, generator: SimpleStatementGenerator | None = None,
                 max_statements: int = 20) -> None:
        if max_statements < 0:
            raise ValueError("max_statements must be non-negative")
        self.adapter = adapter
        self.generator = generator or SimpleStatementGenerator()
        self.max_statements = max_statements

    def run(self, input_path: str | Path, dbms: str) -> QueryRunResult:
        path = Path(input_path).resolve()
        reader = ByteReader.from_file(path)
        dialect = dialect_for(dbms) if dbms in {"postgresql", "mysql"} else dialect_for("postgresql")
        statements: list[str] = []
        traces: list[StatementTrace] = []
        results: list[ExecutionResult] = []
        schema = DatabaseSchema()
        final_status = "INPUT_EXHAUSTED"
        ordinary_error = abnormal_candidate = crash_candidate = False
        started = time.perf_counter()
        alive = False
        try:
            self.adapter.start()
            self.adapter.reset_database()
            schema = self.adapter.query_schema()
            for _ in range(self.max_statements):
                generated = self.generator.generate_with_trace(schema, reader, dialect)
                if generated is None:
                    final_status = "INPUT_EXHAUSTED"
                    break
                statements.append(generated.sql)
                traces.append(StatementTrace(generated.sql, generated.byte_start, generated.byte_end, generated.bytes_consumed))
                result = self.adapter.execute(generated.sql)
                results.append(result)
                if result.status is ExecutionStatus.OK:
                    schema = self.adapter.query_schema()
                    continue
                final_status = result.status.value
                if result.status in self.ORDINARY_ERRORS:
                    ordinary_error = True
                elif result.status is ExecutionStatus.OTHER_ERROR:
                    abnormal_candidate = True
                elif result.status in self.SEVERE_ERRORS:
                    crash_candidate = True
                break
            else:
                final_status = "MAX_STATEMENTS"
            alive = self.adapter.is_alive()
        finally:
            self.adapter.stop()
        valid_count = sum(result.status is ExecutionStatus.OK for result in results)
        # Coverage growth is added by the AFL bridge; this is execution-level eligibility only.
        seed_eligible = valid_count > 0 and not (ordinary_error or abnormal_candidate or crash_candidate)
        return QueryRunResult(
            input_path=str(path), dbms=dbms, statements=tuple(statements), statement_traces=tuple(traces),
            execution_results=tuple(results), final_schema=schema, final_status=final_status,
            duration_ms=(time.perf_counter() - started) * 1000, server_alive=alive,
            bytes_consumed=reader.offset, statement_count=len(statements), valid_statement_count=valid_count,
            ordinary_error=ordinary_error, abnormal_candidate=abnormal_candidate,
            crash_candidate=crash_candidate, seed_eligible=seed_eligible,
        )
