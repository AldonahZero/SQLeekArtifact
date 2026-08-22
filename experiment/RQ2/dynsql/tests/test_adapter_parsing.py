from src.adapters.mysql import MySQLAdapter
from src.adapters.postgresql import PostgreSQLAdapter


def test_postgresql_schema_parser() -> None:
    schema = PostgreSQLAdapter._schema_from_rows("t1\ttable\ta\tinteger\tint4\tYES\n")
    column = schema.relation("t1").columns[0]  # type: ignore[union-attr]
    assert column.normalized_type == "integer"
    assert column.nullable


def test_mysql_schema_parser() -> None:
    schema = MySQLAdapter._schema_from_rows("t1\ttable\ta\tint\tint\tNO\n")
    column = schema.relation("t1").columns[0]  # type: ignore[union-attr]
    assert column.normalized_type == "integer"
    assert not column.nullable
