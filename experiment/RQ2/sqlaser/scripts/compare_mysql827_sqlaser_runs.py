#!/usr/bin/env python3
"""Compare two collected MySQL 8.0.27 SQLRight/SQLaser runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads((path / "summary.json").read_text())


def number(value) -> float:
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlright", type=Path, required=True)
    parser.add_argument("--sqlaser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = read(args.sqlright)
    prototype = read(args.sqlaser)
    baseline_eps = number(baseline.get("execs_per_sec"))
    prototype_eps = number(prototype.get("execs_per_sec"))
    overhead = (baseline_eps - prototype_eps) / baseline_eps if baseline_eps else None
    result = {
        "dbms": "MySQL 8.0.27",
        "sqlright_run": str(args.sqlright),
        "sqlaser_run": str(args.sqlaser),
        "same_cpu": baseline.get("cpu_core") == prototype.get("cpu_core"),
        "same_mysql_commit": baseline.get("mysql_commit") == prototype.get("mysql_commit"),
        "same_sqlright_commit": baseline.get("sqlright_commit") == prototype.get("sqlright_commit"),
        "sqlright": baseline,
        "sqlaser": prototype,
        "overhead": overhead,
        "overhead_definition": "(SQLRight exec/s - SQLaser exec/s) / SQLRight exec/s",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    markdown = [
        "# MySQL 8.0.27 SQLRight vs SQLaser A/B",
        "",
        f"- SQLRight run: `{args.sqlright}`",
        f"- SQLaser run: `{args.sqlaser}`",
        f"- Same CPU: `{result['same_cpu']}`",
        f"- Same MySQL commit: `{result['same_mysql_commit']}`",
        f"- Same SQLRight commit: `{result['same_sqlright_commit']}`",
        "",
        "| Metric | SQLRight | SQLaser |",
        "|---|---:|---:|",
    ]
    fields = [
        ("runtime_seconds", "runtime_seconds"),
        ("execs_done", "execs_done"),
        ("execs_per_sec", "execs_per_sec"),
        ("paths_total", "paths_total"),
        ("queue_files", "queue_files"),
        ("bitmap_cvg", "bitmap_cvg"),
        ("oracle_mutations", "oracle_mutations"),
        ("unique_crashes", "unique_crashes"),
        ("unique_hangs", "unique_hangs"),
    ]
    for label, key in fields:
        markdown.append(f"| {label} | {baseline.get(key, 0)} | {prototype.get(key, 0)} |")
    markdown.extend([
        "",
        f"Trace overhead: `{overhead}`",
        "",
        f"SQLRight distance distribution: `{json.dumps(baseline.get('distance_distribution', {}), sort_keys=True)}`",
        f"SQLaser distance distribution: `{json.dumps(prototype.get('distance_distribution', {}), sort_keys=True)}`",
        f"SQLRight energy distribution: `{json.dumps(baseline.get('energy_distribution', {}), sort_keys=True)}`",
        f"SQLaser energy distribution: `{json.dumps(prototype.get('energy_distribution', {}), sort_keys=True)}`",
        f"SQLaser chain statistics: `{json.dumps(prototype.get('sqlaser', {}), sort_keys=True)}`",
    ])
    args.output.with_suffix(".md").write_text("\n".join(markdown) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
