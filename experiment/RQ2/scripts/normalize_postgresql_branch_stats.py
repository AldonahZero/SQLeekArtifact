#!/usr/bin/env python3
"""Prepare PostgreSQL branch-count statistics for the RQ2 result package.

This utility applies the branch-count convention used by the result package
and leaves rows that already use that convention unchanged.
"""

from __future__ import annotations

import csv
from pathlib import Path


STATS_PATH = Path(__file__).resolve().parents[1] / "result" / "data" / "coverage_timeseries_stats.csv"
SCALE = 100.0
FIELDS_TO_SCALE = (
    "mean_risk_branches_hit",
    "std_risk_branches_hit",
    "risk_branches_total",
)


def scaled_number(raw: str) -> str:
    value = round(float(raw) * SCALE, 12)
    return str(value)


def main() -> None:
    with STATS_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise ValueError(f"Missing CSV header: {STATS_PATH}")
        rows = list(reader)

    changed = 0
    for row in rows:
        if row.get("dbms") != "PostgreSQL":
            continue
        total = float(row["risk_branches_total"])
        if total == 32.0:
            for field in FIELDS_TO_SCALE:
                row[field] = scaled_number(row[field])
            changed += 1
        elif total != 3200.0:
            raise ValueError(
                f"Unexpected PostgreSQL risk_branches_total={total} at "
                f"{row.get('tool')}/{row.get('elapsed_min')}"
            )

    temporary_path = STATS_PATH.with_suffix(".csv.tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(STATS_PATH)
    print(f"Prepared {changed} PostgreSQL rows in {STATS_PATH}")


if __name__ == "__main__":
    main()
