#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path


NUMERIC_FIELDS = [
    "risk_branches_hit",
    "target_region_branch_coverage",
    "risk_targets_hit",
    "target_function_hit_rate",
    "global_branches_hit",
    "global_branch_coverage",
]


def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def stdev(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    mu = mean(vals)
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: finalize_replay_data.py <data-dir> <target-regions.csv>")
    data_dir = Path(sys.argv[1])
    target_regions = Path(sys.argv[2])
    shutil.copy2(target_regions, data_dir / "target_regions.csv")

    groups: dict[tuple[str, str, str, int], list[dict[str, str]]] = defaultdict(list)
    with (data_dir / "coverage_timeseries.csv").open(newline="") as fp:
        for row in csv.DictReader(fp):
            key = (row["tool"], row["dbms"], row["elapsed_min"], int(row["elapsed_min"]))
            groups[key].append(row)

    fields = ["tool", "dbms", "elapsed_min", "repeats"]
    for field in NUMERIC_FIELDS:
        fields.extend([field + "_mean", field + "_std"])

    rows = []
    for (tool, dbms, elapsed_min, elapsed_sort), vals in sorted(groups.items(), key=lambda item: item[0][3]):
        out = {"tool": tool, "dbms": dbms, "elapsed_min": elapsed_min, "repeats": len(vals)}
        for field in NUMERIC_FIELDS:
            numbers = [float(row[field]) for row in vals if row.get(field) not in ("", None)]
            out[field + "_mean"] = mean(numbers)
            out[field + "_std"] = stdev(numbers)
        rows.append(out)

    with (data_dir / "coverage_timeseries_stats.csv").open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
