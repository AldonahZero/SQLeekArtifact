import os
from pathlib import Path

import pytest

from src.adapters.mysql import MySQLAdapter
from src.adapters.postgresql import PostgreSQLAdapter
from src.core import ExecutionStatus

pytestmark = pytest.mark.skipif(os.environ.get("DYNSQL_RUN_INTEGRATION") != "1", reason="set DYNSQL_RUN_INTEGRATION=1")


@pytest.mark.parametrize("adapter_cls", [PostgreSQLAdapter, MySQLAdapter])
def test_adapter_lifecycle(adapter_cls) -> None:
    root = Path(__file__).resolve().parents[1]
    adapter = adapter_cls(root)
    try:
        adapter.start()
        adapter.reset_database()
        assert adapter.execute("CREATE TABLE t1(a INTEGER, b TEXT);").status is ExecutionStatus.OK
        assert adapter.execute("CREATE VIEW v1 AS SELECT a FROM t1;").status is ExecutionStatus.OK
        assert adapter.query_schema().relation("t1") is not None
        assert adapter.execute("SELEC broken;").status is ExecutionStatus.SYNTAX_ERROR
        assert adapter.execute("SELECT * FROM missing_table;").status is ExecutionStatus.SEMANTIC_ERROR
        assert adapter.is_alive()
    finally:
        adapter.stop()
