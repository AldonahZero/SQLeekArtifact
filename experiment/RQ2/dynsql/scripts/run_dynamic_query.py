#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.mysql import MySQLAdapter
from src.adapters.postgresql import PostgreSQLAdapter
from src.scheduler import DynamicQueryScheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one deterministic DynSQL dynamic query")
    parser.add_argument("--dbms", required=True, choices=("postgresql", "mysql"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--max-statements", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    adapter_cls = PostgreSQLAdapter if args.dbms == "postgresql" else MySQLAdapter
    scheduler = DynamicQueryScheduler(adapter_cls(PROJECT_ROOT), max_statements=args.max_statements)
    result = scheduler.run(input_path, args.dbms)
    output_path = args.output
    if output_path is None:
        output_path = PROJECT_ROOT / "output/dynamic-query" / f"{args.dbms}-{input_path.stem}.json"
    elif not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    output_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(f"result: {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
