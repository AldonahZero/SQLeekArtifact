from src.core import Column, DatabaseSchema, ExecutionResult, ExecutionStatus, Relation


def test_schema_lookup_and_immutable_models() -> None:
    column = Column("id", "integer", "int4", False)
    relation = Relation("items", "table", (column,))
    schema = DatabaseSchema((relation,))
    assert schema.relation("items") == relation
    assert schema.relation("missing") is None


def test_execution_result_defaults() -> None:
    result = ExecutionResult(ExecutionStatus.OK)
    assert result.status is ExecutionStatus.OK
    assert result.sqlstate is None


def test_relation_rejects_unknown_kind() -> None:
    try:
        Relation("x", "sequence")
    except ValueError:
        return
    raise AssertionError("invalid relation kind was accepted")
