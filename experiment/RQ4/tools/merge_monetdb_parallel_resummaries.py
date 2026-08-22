#!/usr/bin/env python3
"""Merge one-repeat MonetDB LLVM summaries produced in parallel.

The replay itself is independent for each repeat.  This helper combines the
five one-repeat resummarizer outputs without changing the original replay
queues or the profdata files, and recomputes the component-level mean table.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path


def read_csv(path: Path, delimiter: str = ",") -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        return list(reader), list(reader.fieldnames or [])


def write_csv(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: list[str],
    delimiter: str = ",",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter=delimiter,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def merge_rows(
    inputs: list[Path],
    output: Path,
    *,
    delimiter: str = ",",
    sort_key=None,
) -> tuple[int, list[str]]:
    rows: list[dict[str, str]] = []
    fields: list[str] = []
    for path in inputs:
        part, part_fields = read_csv(path, delimiter=delimiter)
        rows.extend(part)
        for field in part_fields:
            if field not in fields:
                fields.append(field)
    if sort_key is not None:
        rows.sort(key=sort_key)
    write_csv(output, rows, fields, delimiter=delimiter)
    return len(rows), fields


def number(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return f"{value:.15f}".rstrip("0").rstrip(".")


def component_aggregate(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["dbms"], row["component"], row["tool"])].append(row)

    output: list[dict[str, object]] = []
    for (dbms, component, tool), bucket in sorted(groups.items()):
        branch_total = max(int(float(row["risk_branches_total"])) for row in bucket)
        branch_hit = sum(float(row["risk_branches_hit"]) for row in bucket) / len(bucket)
        target_total = max(int(float(row["risk_targets_total"])) for row in bucket)
        target_hit = sum(float(row["risk_targets_hit"]) for row in bucket) / len(bucket)
        function_rate = sum(
            float(row["target_function_hit_rate"]) for row in bucket
        ) / len(bucket)
        output.append(
            {
                "dbms": dbms,
                "component": component,
                "tool": tool,
                "risk_branches_total": branch_total,
                "risk_branches_hit": number(branch_hit),
                "target_region_branch_coverage": number(
                    branch_hit / branch_total if branch_total else 0.0
                ),
                "risk_targets_total": target_total,
                "risk_targets_hit": number(target_hit),
                "target_function_hit_rate": number(function_rate),
            }
        )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary_root = args.summary_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repeat_dirs = [summary_root / f"r{i}" for i in range(1, args.repeats + 1)]
    missing = [str(path) for path in repeat_dirs if not path.is_dir()]
    if missing:
        raise SystemExit(f"missing repeat summary directories: {', '.join(missing)}")

    csv_names = [
        "runs.csv",
        "coverage_summary.csv",
        "coverage_timeseries.csv",
        "target_region_hits.csv",
        "target_branch_hits.csv",
        "component_heatmap_by_run.csv",
    ]
    counts: dict[str, int] = {}
    for name in csv_names:
        count, _ = merge_rows(
            [path / name for path in repeat_dirs],
            output_dir / name,
            sort_key=(
                (lambda row: (
                    int(float(row.get("repeat_id", "0"))),
                    int(float(row.get("elapsed_min", "0"))),
                    row.get("run_id", ""),
                ))
                if name in {"runs.csv", "coverage_summary.csv", "coverage_timeseries.csv"}
                else None
            ),
        )
        counts[name] = count

    component_rows, component_fields = read_csv(output_dir / "component_heatmap_by_run.csv")
    write_csv(
        output_dir / "component_heatmap.csv",
        component_aggregate(component_rows),
        [
            "dbms",
            "component",
            "tool",
            "risk_branches_total",
            "risk_branches_hit",
            "target_region_branch_coverage",
            "risk_targets_total",
            "risk_targets_hit",
            "target_function_hit_rate",
        ],
    )

    index_inputs = [path / "replay_index.tsv" for path in repeat_dirs]
    index_count, index_fields = merge_rows(
        index_inputs,
        output_dir / "replay_index.tsv",
        delimiter="\t",
        sort_key=lambda row: (
            int(float(row.get("repeat_id", "0"))),
            int(float(row.get("checkpoint_min", "0"))),
        ),
    )
    target_regions = repeat_dirs[0] / "target_regions.csv"
    if target_regions.exists():
        shutil.copy2(target_regions, output_dir / "target_regions.csv")

    summary_rows, _ = read_csv(output_dir / "coverage_summary.csv")
    timeseries_rows, _ = read_csv(output_dir / "coverage_timeseries.csv")
    repeats = sorted({int(float(row["repeat_id"])) for row in summary_rows})
    checkpoints = sorted({int(float(row["elapsed_min"])) for row in timeseries_rows})
    expected = list(range(1, args.repeats + 1))
    if repeats != expected:
        raise SystemExit(f"unexpected repeats: {repeats}, expected {expected}")
    if len(timeseries_rows) != args.repeats * len(checkpoints):
        raise SystemExit("timeseries does not contain one row per repeat/checkpoint")

    report = {
        "summary_root": str(summary_root),
        "output_dir": str(output_dir),
        "repeats": repeats,
        "checkpoints_min": checkpoints,
        "rows": {**counts, "component_heatmap": len(component_aggregate(component_rows)), "replay_index": index_count},
        "fields": {"component_heatmap_by_run": component_fields, "replay_index": index_fields},
    }
    (output_dir / "parallel_merge_summary.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
