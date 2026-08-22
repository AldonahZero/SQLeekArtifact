#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.mysql import MySQLAdapter
from src.adapters.postgresql import PostgreSQLAdapter
from src.core import ExecutionStatus


def run_one(name: str) -> dict[str, object]:
    adapter_cls = PostgreSQLAdapter if name == "postgresql" else MySQLAdapter
    adapter = adapter_cls(PROJECT_ROOT)
    summary: dict[str, object] = {"dbms": name, "passed": False}
    try:
        adapter.start()
        adapter.reset_database()
        table = adapter.execute("CREATE TABLE t1(a INTEGER, b TEXT);")
        view = adapter.execute("CREATE VIEW v1 AS SELECT a FROM t1;")
        assert table.status is ExecutionStatus.OK, table
        assert view.status is ExecutionStatus.OK, view
        schema = adapter.query_schema()
        t1, v1 = schema.relation("t1"), schema.relation("v1")
        assert t1 is not None and t1.kind == "table"
        assert v1 is not None and v1.kind == "view"
        assert [column.name for column in t1.columns] == ["a", "b"]
        syntax = adapter.execute("SELEC broken;")
        semantic = adapter.execute("SELECT * FROM table_that_does_not_exist;")
        assert syntax.status is ExecutionStatus.SYNTAX_ERROR, syntax
        assert semantic.status is ExecutionStatus.SEMANTIC_ERROR, semantic
        assert adapter.is_alive()
        summary.update(
            passed=True,
            schema=[{"name": relation.name, "kind": relation.kind,
                     "columns": [{"name": c.name, "normalized_type": c.normalized_type,
                                  "native_type": c.native_type, "nullable": c.nullable} for c in relation.columns]}
                    for relation in schema.relations],
            syntax_error={"status": syntax.status.value, "sqlstate": syntax.sqlstate,
                          "error_code": syntax.error_code, "message": syntax.message},
            semantic_error={"status": semantic.status.value, "sqlstate": semantic.sqlstate,
                            "error_code": semantic.error_code, "message": semantic.message},
            connection=adapter.get_connection_info(),
        )
    except Exception as exc:
        summary.update(error=str(exc), traceback=traceback.format_exc())
    finally:
        adapter.stop()
        summary["stopped"] = not adapter.is_alive()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DynSQL DBMS adapter smoke tests")
    parser.add_argument("dbms", choices=("postgresql", "mysql", "all"))
    args = parser.parse_args()
    names = ["postgresql", "mysql"] if args.dbms == "all" else [args.dbms]
    results = [run_one(name) for name in names]
    log_dir = PROJECT_ROOT / "logs/adapter-smoke"
    log_dir.mkdir(parents=True, exist_ok=True)
    output = json.dumps(results, indent=2, ensure_ascii=False)
    (log_dir / "adapter-smoke-results.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if all(item.get("passed") and item.get("stopped") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
