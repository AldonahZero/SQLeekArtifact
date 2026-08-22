from pathlib import Path

from src.core import Column, DatabaseSchema, ExecutionResult, ExecutionStatus, Relation
from src.generator import ByteReader, InputExhausted, SimpleStatementGenerator
from src.scheduler import DynamicQueryScheduler


def test_byte_reader_determinism_and_exhaustion() -> None:
    first, second = ByteReader(bytes([1, 2])), ByteReader(bytes([1, 2]))
    assert [first.next_byte(), first.next_byte()] == [second.next_byte(), second.next_byte()] == [1, 2]
    try:
        first.next_byte()
    except InputExhausted:
        return
    raise AssertionError("exhaustion was not reported")


def test_choose_boundaries() -> None:
    reader = ByteReader(bytes([0, 255]))
    assert reader.choose(("a", "b")) == "a"
    assert reader.choose(("a", "b")) == "b"
    try:
        ByteReader(b"x").choose(())
    except ValueError:
        return
    raise AssertionError("empty options were accepted")


def test_same_input_same_sql() -> None:
    schema = DatabaseSchema((Relation("t_1", "table", (Column("c_1", "integer", "int", True),)),))
    generator = SimpleStatementGenerator()
    assert generator.generate(schema, ByteReader(bytes([3, 0, 0, 0]))) == generator.generate(schema, ByteReader(bytes([3, 0, 0, 0])))


def test_schema_change_is_used() -> None:
    generator = SimpleStatementGenerator()
    reader = ByteReader(bytes([0, 7, 8, 0, 2, 0, 0, 0]))
    create = generator.generate(DatabaseSchema(), reader)
    assert create == "CREATE TABLE t_7(c_8 INTEGER);"
    schema = DatabaseSchema((Relation("t_7", "table", (Column("c_8", "integer", "INTEGER", True),)),))
    assert generator.generate(schema, reader).startswith("INSERT INTO t_7(c_8)")


def test_statement_byte_trace_and_select_features() -> None:
    schema = DatabaseSchema((
        Relation("t_1", "table", (Column("c_1", "integer", "INTEGER", True),)),
        Relation("t_2", "table", (Column("c_2", "integer", "INTEGER", True),)),
    ))
    generated = SimpleStatementGenerator().generate_with_trace(schema, ByteReader(bytes([3, 0, 1, 31])))
    assert generated is not None
    assert (generated.byte_start, generated.byte_end, generated.bytes_consumed) == (0, 4, 4)
    assert " JOIN " in generated.sql
    assert " WHERE " in generated.sql
    assert " ORDER BY " in generated.sql
    assert " LIMIT " in generated.sql
    assert "(SELECT " in generated.sql
    assert len(generated.sql) <= 4096


class FakeAdapter:
    database_name = "dynsql_test"

    def __init__(self, results=None) -> None:
        self.alive = False
        self.schema = DatabaseSchema()
        self.results = list(results or [])
        self.query_count = 0

    def start(self): self.alive = True
    def stop(self): self.alive = False
    def is_alive(self): return self.alive
    def reset_database(self): self.schema = DatabaseSchema()
    def get_server_log(self): return ""
    def get_connection_info(self): return {}
    def query_schema(self): self.query_count += 1; return self.schema
    def execute(self, statement):
        if self.results:
            result = self.results.pop(0)
            if result.status is ExecutionStatus.SERVER_CRASH:
                self.alive = False
            return result
        if statement.startswith("CREATE TABLE"):
            self.schema = DatabaseSchema((Relation("t_0", "table", (Column("c_0", "integer", "INTEGER", True),)),))
        return ExecutionResult(ExecutionStatus.OK, server_alive=True)


def test_max_statements_limit(tmp_path: Path) -> None:
    seed = tmp_path / "seed.bin"; seed.write_bytes(bytes(40))
    result = DynamicQueryScheduler(FakeAdapter(), max_statements=2).run(seed, "fake")
    assert len(result.statements) == 2
    assert result.final_status == "MAX_STATEMENTS"


def test_ordinary_error_terminates(tmp_path: Path) -> None:
    seed = tmp_path / "seed.bin"; seed.write_bytes(bytes(12))
    adapter = FakeAdapter([ExecutionResult(ExecutionStatus.SEMANTIC_ERROR, server_alive=True)])
    result = DynamicQueryScheduler(adapter).run(seed, "fake")
    assert len(result.statements) == 1
    assert result.final_status == "SEMANTIC_ERROR"
    assert result.ordinary_error
    assert not result.seed_eligible
    assert not adapter.alive


def test_server_crash_propagates(tmp_path: Path) -> None:
    seed = tmp_path / "seed.bin"; seed.write_bytes(bytes(12))
    adapter = FakeAdapter([ExecutionResult(ExecutionStatus.SERVER_CRASH, server_alive=False)])
    result = DynamicQueryScheduler(adapter).run(seed, "fake")
    assert result.final_status == "SERVER_CRASH"
    assert not result.server_alive
    assert not adapter.alive


def test_all_severe_statuses_propagate(tmp_path: Path) -> None:
    seed = tmp_path / "severe.bin"; seed.write_bytes(bytes(12))
    for status in (ExecutionStatus.TIMEOUT, ExecutionStatus.CONNECTION_LOST, ExecutionStatus.SERVER_CRASH):
        adapter = FakeAdapter([ExecutionResult(status, server_alive=status is not ExecutionStatus.SERVER_CRASH)])
        result = DynamicQueryScheduler(adapter).run(seed, "fake")
        assert result.final_status == status.value
        assert result.crash_candidate
        assert not result.seed_eligible
        assert len(result.statements) == 1
        assert not adapter.alive


def test_other_error_is_abnormal_candidate(tmp_path: Path) -> None:
    seed = tmp_path / "abnormal.bin"; seed.write_bytes(bytes(12))
    adapter = FakeAdapter([ExecutionResult(ExecutionStatus.OTHER_ERROR, server_alive=True)])
    result = DynamicQueryScheduler(adapter).run(seed, "fake")
    assert result.final_status == "OTHER_ERROR"
    assert result.abnormal_candidate
    assert not result.ordinary_error
    assert not result.crash_candidate
    assert not result.seed_eligible
