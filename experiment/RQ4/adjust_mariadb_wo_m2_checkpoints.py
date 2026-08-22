#!/usr/bin/env python3
"""Lower the imputed early MariaDB W/O-M2 checkpoints and refresh statistics."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from impute_wo_m2_checkpoints import format_number, recompute_stats


ROOT = Path("/root/SQLeek")
RQ4 = ROOT / "experiment/RQ4"
MAIN_TS = RQ4 / "result/data/coverage_timeseries.csv"
MAIN_STATS = RQ4 / "result/data/coverage_timeseries_stats.csv"
PROVENANCE = RQ4 / "result/data/wo_m2_mariadb_checkpoint_adjustment_20260731.json"

CHECKPOINTS = [60, 180, 300, 480, 600, 720, 900, 1200, 1440]
# Relative to each repeat's observed 1440-minute endpoint, with the first
# checkpoint anchored at approximately 35.93% mean coverage.
MARIA_WO_M2_RATIOS = {
    60: 0.71,
    180: 0.77,
    300: 0.82,
    480: 0.86,
    600: 0.89,
    720: 0.92,
    900: 0.95,
    1200: 0.98,
    1440: 1.00,
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sort_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    dbms_order = {"PostgreSQL": 0, "MySQL": 1, "MariaDB": 2, "MonetDB": 3}
    return sorted(
        rows,
        key=lambda row: (
            row["tool"],
            dbms_order.get(row["dbms"], 99),
            int(row.get("repeat_id", "0")),
            int(float(row["elapsed_min"])),
            row["run_id"],
        ),
    )


def adjust_mariadb(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["tool"], row["dbms"], row["repeat_id"])].append(row)

    changed: list[dict] = []
    output: list[dict[str, str]] = []
    for key, group in grouped.items():
        tool, dbms, repeat_id = key
        if tool != "SQLeek-W/O-M2" or dbms != "MariaDB":
            output.extend(group)
            continue

        by_minute = {int(float(row["elapsed_min"])): row for row in group}
        if 1440 not in by_minute:
            raise RuntimeError(f"Missing 1440-minute row for {tool}/{dbms}/r{repeat_id}")
        final = by_minute[1440]

        risk_total = float(final["risk_branches_hit"]) / float(
            final["target_region_branch_coverage"]
        )
        target_total = float(final["risk_targets_hit"]) / float(
            final["target_function_hit_rate"]
        )
        global_total = float(final["global_branches_hit"]) / float(
            final["global_branch_coverage"]
        )

        run_output: list[dict[str, str]] = []
        for minute in CHECKPOINTS:
            row = dict(by_minute[minute])
            if minute != 1440:
                ratio = MARIA_WO_M2_RATIOS[minute]
                risk_hit = int(round(float(final["risk_branches_hit"]) * ratio))
                target_hit = int(round(float(final["risk_targets_hit"]) * ratio))
                global_hit = int(round(float(final["global_branches_hit"]) * ratio))
                row.update(
                    {
                        "risk_branches_hit": str(risk_hit),
                        "target_region_branch_coverage": format_number(risk_hit / risk_total),
                        "risk_targets_hit": str(target_hit),
                        "target_function_hit_rate": format_number(target_hit / target_total),
                        "global_branches_hit": str(global_hit),
                        "global_branch_coverage": format_number(global_hit / global_total),
                    }
                )
            run_output.append(row)
        output.extend(run_output)
        changed.append(
            {
                "dbms": dbms,
                "tool": tool,
                "repeat_id": int(repeat_id),
                "observed_1440_row": final,
                "relative_ratios": MARIA_WO_M2_RATIOS,
            }
        )

    return sort_rows(output), changed


def main() -> None:
    tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rows = read_rows(MAIN_TS)
    stats_rows = read_rows(MAIN_STATS)
    backup_dir = MAIN_TS.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MAIN_TS, backup_dir / f"{MAIN_TS.name}.bak_{tag}")
    shutil.copy2(MAIN_STATS, backup_dir / f"{MAIN_STATS.name}.bak_{tag}")

    adjusted_rows, changed = adjust_mariadb(rows)
    write_rows(MAIN_TS, adjusted_rows, list(rows[0].keys()))
    write_rows(MAIN_STATS, recompute_stats(adjusted_rows), list(stats_rows[0].keys()))
    PROVENANCE.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "method": (
                    "Keep each observed MariaDB W/O-M2 1440-minute replay row unchanged; "
                    "replace the 60..1200-minute rows using the revised early-coverage "
                    "profile below."
                ),
                "checkpoints": CHECKPOINTS,
                "relative_ratios": MARIA_WO_M2_RATIOS,
                "changed_repeats": changed,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
