#!/usr/bin/env python3
"""Import available RQ4 component replay summaries into the heatmap input.

This script only adds component-level evidence that exists on the server.  It
does not infer missing component rows from an overall coverage percentage.
The source replay packages use legacy labels (``SQLeek``, lowercase DBMS names,
and underscore-separated component names), so they are canonicalized here.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


ROOT = Path("/root/SQLeek")
DATA = ROOT / "experiment/RQ4/result/data"
TARGET = DATA / "component_heatmap.csv"

SOURCE_GROUPS = {
    ("MariaDB", "SQLeek-W/O-M2"): [
        ROOT
        / "experiment/RQ4/replay/output/rq4_wo_m2_seq_replay_20260729_131551/mariadb/real/component_heatmap.csv",
        ROOT
        / "experiment/RQ4/replay/output/rq4_wo_m2_mariadb_seq_replay_20260729_170725/r2/real/component_heatmap.csv",
        ROOT
        / "experiment/RQ4/replay/output/rq4_wo_m2_mariadb_seq_replay_20260729_170725/r3/real/component_heatmap.csv",
        ROOT
        / "experiment/RQ4/replay/output/rq4_wo_m2_mariadb_seq_replay_20260729_170725/r4/real/component_heatmap.csv",
    ],
    ("MonetDB", "SQLeek-W/O-M2"): [
        ROOT
        / "experiment/RQ4/replay/output/rq4_wo_m2_seq_replay_20260729_131551/monetdb/real/component_heatmap.csv",
    ],
}

FIELDNAMES = [
    "dbms",
    "component",
    "tool",
    "risk_branches_total",
    "risk_branches_hit",
    "target_region_branch_coverage",
    "risk_targets_total",
    "risk_targets_hit",
    "target_function_hit_rate",
]

DBMS_ORDER = {"PostgreSQL": 0, "MySQL": 1, "MariaDB": 2, "MonetDB": 3}
TOOL_ORDER = {
    "SQLeek-Full": 0,
    "SQLeek-W/O-M1": 1,
    "SQLeek-W/O-M2": 2,
    "SQLeek-W/O-M3": 3,
}
COMPONENT_ORDER = [
    "catalog/metadata",
    "cursor/prepared stmt",
    "executor",
    "optimizer",
    "other",
    "parser",
    "storage",
    "type system",
]

COMPONENT_MAP = {
    "catalog_metadata": "catalog/metadata",
    "catalog/metadata": "catalog/metadata",
    "cursor_prepared": "cursor/prepared stmt",
    "cursor/prepared stmt": "cursor/prepared stmt",
    "type_system": "type system",
    "type system": "type system",
}


def number(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return f"{value:.15f}".rstrip("0").rstrip(".")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def canonical_component(value: str) -> str:
    return COMPONENT_MAP.get(value.strip(), value.strip())


def aggregate_source_rows(
    dbms: str, tool: str, source_paths: list[Path]
) -> tuple[list[dict[str, str]], dict[str, object]]:
    source_rows: list[dict[str, str]] = []
    for path in source_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        source_rows.extend(read_csv(path))

    by_component: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        component = canonical_component(row.get("component", ""))
        if component not in COMPONENT_ORDER:
            raise ValueError(f"Unexpected component {component!r} in {source_paths}")
        by_component[component].append(row)

    output: list[dict[str, str]] = []
    for component in COMPONENT_ORDER:
        rows = by_component.get(component, [])
        if not rows:
            raise ValueError(f"Missing {dbms} {tool} component {component}")

        branch_total = mean(float(row["risk_branches_total"]) for row in rows)
        branch_hit = mean(float(row["risk_branches_hit"]) for row in rows)
        target_total = mean(float(row["risk_targets_total"]) for row in rows)
        target_hit = mean(float(row["risk_targets_hit"]) for row in rows)
        output.append(
            {
                "dbms": dbms,
                "component": component,
                "tool": tool,
                "risk_branches_total": number(branch_total),
                "risk_branches_hit": number(branch_hit),
                "target_region_branch_coverage": number(
                    branch_hit / branch_total
                ),
                "risk_targets_total": number(target_total),
                "risk_targets_hit": number(target_hit),
                "target_function_hit_rate": number(target_hit / target_total),
            }
        )

    provenance = {
        "dbms": dbms,
        "tool": tool,
        "source_paths": [str(path) for path in source_paths],
        "source_rows": len(source_rows),
        "source_repeats": len(source_paths),
        "components": len(output),
    }
    return output, provenance


def row_sort_key(row: dict[str, str]) -> tuple[int, int, int]:
    return (
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        COMPONENT_ORDER.index(row.get("component", ""))
        if row.get("component", "") in COMPONENT_ORDER
        else 99,
        TOOL_ORDER.get(row.get("tool", ""), 99),
    )


def main() -> None:
    if not TARGET.is_file():
        raise SystemExit(f"missing heatmap input: {TARGET}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = DATA / "backups" / f"before_component_heatmap_completion_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(TARGET, backup_dir / TARGET.name)

    current_rows = read_csv(TARGET)
    imported_rows: list[dict[str, str]] = []
    provenance: list[dict[str, object]] = []
    imported_keys = set(SOURCE_GROUPS)
    for key, paths in SOURCE_GROUPS.items():
        rows, record = aggregate_source_rows(*key, paths)
        imported_rows.extend(rows)
        provenance.append(record)

    kept_rows = [
        row
        for row in current_rows
        if (row.get("dbms", ""), row.get("tool", "")) not in imported_keys
    ]
    merged_rows = sorted(kept_rows + imported_rows, key=row_sort_key)
    with TARGET.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged_rows)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation": "complete_rq4_component_heatmap_from_existing_replays",
        "backup": str(backup_dir),
        "rows_before": len(current_rows),
        "rows_after": len(merged_rows),
        "rows_added": len(imported_rows),
        "imports": provenance,
        "not_inferred": ["MonetDB SQLeek-W/O-M3"],
    }
    manifest_path = DATA / f"component_heatmap_completion_{timestamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
