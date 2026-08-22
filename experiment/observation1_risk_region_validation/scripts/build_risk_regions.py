#!/usr/bin/env python3
"""Build stable risk-region records from SQLeek Stage 1 outputs."""
from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    canonical_dbms,
    component_from_path,
    data_dir,
    enclosing_function_from_file,
    find_source_file,
    git,
    load_config,
    normalize_path,
    parse_codeql_csv,
    stable_hash,
    stable_region_id,
    stage1_dbms,
    write_csv,
)


RULE_FILES = [
    ("dbms_stale_descriptor", "stale_descriptor"),
    ("dbms_memory_sinks", "memory_sink"),
]


def severity_score(severity: str) -> float:
    value = severity.lower()
    if value == "error":
        return 3.0
    if value == "warning":
        return 2.0
    if value == "recommendation":
        return 1.0
    return 1.0


def build_regions(dbms: str) -> list[dict[str, object]]:
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    source_commit = git(repo, "rev-parse", "HEAD").strip()
    stage_dir = Path(str(cfg["stage1_codeql_results_dir"]))
    rows: list[dict[str, object]] = []
    seen: dict[str, dict[str, object]] = {}
    for rule_id, risk_type in RULE_FILES:
        csv_path = stage_dir / f"{rule_id}.csv"
        for alert in parse_codeql_csv(csv_path):
            file_path = normalize_path(str(alert["file_path"]))
            start_line = int(alert["start_line"])
            end_line = int(alert["end_line"]) or start_line
            if not file_path or start_line <= 0:
                continue
            if end_line < start_line:
                end_line = start_line
            region_id = stable_region_id(dbms, file_path, start_line, end_line, rule_id)
            alert_id = f"{dbms}_{rule_id}_{stable_hash(str(alert), 20)}"
            if region_id in seen:
                prev = seen[region_id]
                prev["alert_id"] = f"{prev['alert_id']};{alert_id}"
                prev["risk_score"] = max(float(prev["risk_score"]), severity_score(str(alert["severity"])))
                continue
            source_file = find_source_file(cfg, file_path)
            function, function_loc = enclosing_function_from_file(source_file, start_line)
            row = {
                "dbms": canonical_dbms(dbms),
                "region_id": region_id,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "region_loc": max(1, end_line - start_line + 1),
                "enclosing_function": function,
                "enclosing_function_loc": function_loc,
                "component": component_from_path(file_path),
                "rule_id": rule_id,
                "alert_id": alert_id,
                "risk_type": risk_type,
                "risk_score": severity_score(str(alert["severity"])),
                "source_commit": source_commit,
                "region_definition_method": "stage1_codeql_source_range",
                "alert_message": alert["message"],
                "stage1_csv": str(csv_path),
                "stage1_csv_row": alert["csv_row"],
            }
            seen[region_id] = row
            rows.append(row)
    rows.sort(key=lambda r: (str(r["file_path"]), int(r["start_line"]), str(r["rule_id"])))
    return rows


FIELDS = [
    "dbms",
    "region_id",
    "file_path",
    "start_line",
    "end_line",
    "region_loc",
    "enclosing_function",
    "enclosing_function_loc",
    "component",
    "rule_id",
    "alert_id",
    "risk_type",
    "risk_score",
    "source_commit",
    "region_definition_method",
    "alert_message",
    "stage1_csv",
    "stage1_csv_row",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dbms = canonical_dbms(args.dbms)
    out = data_dir(dbms) / "risk_regions.csv"
    if out.is_file() and not args.force:
        print(out)
        return
    rows = build_regions(dbms)
    write_csv(out, rows, FIELDS)
    print(f"{out} regions={len(rows)}")


if __name__ == "__main__":
    main()
