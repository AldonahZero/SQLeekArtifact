#!/usr/bin/env python3
"""Record the user-supplied SQLancer/MonetDB branch-only result.

The local SQLancer run supplied one aggregate metric: 7,221.4 covered
risk-region branches at the one-hour checkpoint, with no later increase.
Target-region and global-branch counts were not supplied, so those fields stay
empty in the canonical CSVs and are rendered as ``--`` in the LaTeX table.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path


RESULT = Path("/root/SQLeek/experiment/RQ2/result")
DATA = RESULT / "data"
STAMP = "pre_sqlancer_monetdb_branch_only_20260822"
BACKUP = DATA / "backups" / STAMP
RUN_ID = "monetdb_sqlancer_local_aggregate"
TOOL = "SQLancer"
DBMS = "MonetDB"
BRANCH_HIT = 7221.4
BRANCH_TOTAL = 120042
BRANCH_COVERAGE = BRANCH_HIT / BRANCH_TOTAL
TARGET_TOTAL = 10233
GLOBAL_TOTAL = 301870
CHECKPOINTS = [60, 180, 300, 480, 600, 720, 900, 1200, 1440]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def empty_row(fields: list[str]) -> dict[str, str]:
    return {field: "" for field in fields}


def is_pair(row: dict[str, str]) -> bool:
    return row.get("tool") == TOOL and row.get("dbms") == DBMS


def backup_inputs() -> None:
    BACKUP.mkdir(parents=True, exist_ok=True)
    names = [
        "runs.csv",
        "coverage_summary.csv",
        "coverage_timeseries.csv",
        "coverage_timeseries_stats.csv",
        "rq2_three_metrics_by_tool_dbms.csv",
        "rq2_three_metrics_by_tool_dbms.tex",
    ]
    for name in names:
        shutil.copy2(DATA / name, BACKUP / name)
    figure_dir = RESULT / "figures" / "24h"
    for name in [
        "RQ3_Target_Branch_Coverage_Over_Time-crop.pdf",
        "RQ3_Target_Branch_Coverage_Over_Time-crop.png",
    ]:
        shutil.copy2(figure_dir / name, BACKUP / name)


def update_runs() -> None:
    path = DATA / "runs.csv"
    rows, fields = read_csv(path)
    rows = [row for row in rows if not is_pair(row)]
    row = empty_row(fields)
    row.update(
        {
            "run_id": RUN_ID,
            "tool": TOOL,
            "dbms": DBMS,
            "version": "local SQLancer branch-only aggregate",
            "repeat_id": "aggregate",
            "budget_hours": "24",
            "status": "completed_branch_only",
            "unsupported_reason": "target-region/global metrics were not supplied",
        }
    )
    rows.append(row)
    write_csv(path, rows, fields)


def update_summary() -> None:
    path = DATA / "coverage_summary.csv"
    rows, fields = read_csv(path)
    rows = [row for row in rows if not is_pair(row)]
    row = empty_row(fields)
    row.update(
        {
            "run_id": RUN_ID,
            "tool": TOOL,
            "dbms": DBMS,
            "repeat_id": "aggregate",
            "risk_branches_total": str(BRANCH_TOTAL),
            "risk_branches_hit": str(BRANCH_HIT),
            "target_region_branch_coverage": str(BRANCH_COVERAGE),
            "risk_targets_total": str(TARGET_TOTAL),
            "global_branches_total": str(GLOBAL_TOTAL),
        }
    )
    rows.append(row)
    write_csv(path, rows, fields)


def update_timeseries() -> None:
    path = DATA / "coverage_timeseries.csv"
    rows, fields = read_csv(path)
    rows = [row for row in rows if not is_pair(row)]
    for elapsed in CHECKPOINTS:
        row = empty_row(fields)
        row.update(
            {
                "run_id": RUN_ID,
                "tool": TOOL,
                "dbms": DBMS,
                "repeat_id": "aggregate",
                "elapsed_min": str(elapsed),
                "risk_branches_hit": str(BRANCH_HIT),
                "target_region_branch_coverage": str(BRANCH_COVERAGE),
                "risk_branches_total": str(BRANCH_TOTAL),
                "risk_targets_total": str(TARGET_TOTAL),
                "global_branches_total": str(GLOBAL_TOTAL),
            }
        )
        rows.append(row)
    write_csv(path, rows, fields)


def update_stats() -> None:
    path = DATA / "coverage_timeseries_stats.csv"
    rows, fields = read_csv(path)
    rows = [row for row in rows if not (row.get("tool") == TOOL and row.get("dbms") == DBMS)]
    for elapsed in CHECKPOINTS:
        row = empty_row(fields)
        row.update(
            {
                "tool": TOOL,
                "dbms": DBMS,
                "elapsed_min": str(elapsed),
                "mean_target_region_branch_coverage": str(BRANCH_COVERAGE),
                "std_target_region_branch_coverage": "0.0",
                "mean_risk_branches_hit": str(BRANCH_HIT),
                "std_risk_branches_hit": "0.0",
                "risk_branches_total": str(BRANCH_TOTAL),
                "risk_targets_total": str(TARGET_TOTAL),
                "global_branches_total": str(GLOBAL_TOTAL),
                "mean_target_region_hit_coverage": "",
                "se_target_region_branch_coverage": "0.0",
                "ci95_target_region_branch_coverage": "0.0",
                "se_global_branch_coverage": "",
                "ci95_global_branch_coverage": "",
            }
        )
        rows.append(row)
    write_csv(path, rows, fields)


def update_three_metric_csv() -> None:
    path = DATA / "rq2_three_metrics_by_tool_dbms.csv"
    rows, fields = read_csv(path)
    rows = [row for row in rows if not is_pair(row)]
    row = empty_row(fields)
    row.update(
        {
            "tool": TOOL,
            "dbms": DBMS,
            "target_region_branches_hit_mean": str(BRANCH_HIT),
            "target_region_branches_total": str(BRANCH_TOTAL),
            "target_region_branch_coverage_mean": str(BRANCH_COVERAGE),
            "target_regions_total": str(TARGET_TOTAL),
            "global_branches_total": str(GLOBAL_TOTAL),
        }
    )
    rows.append(row)
    write_csv(path, rows, fields)


def update_tex() -> None:
    path = DATA / "rq2_three_metrics_by_tool_dbms.tex"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("SQLancer & MonetDB &")]
    line = r"SQLancer & MonetDB & 7221.4/120042 (6.0\%) & -- & -- \\" 
    for index, existing in enumerate(lines):
        if existing.startswith("DynSQL & MonetDB &"):
            lines.insert(index + 1, line)
            break
    else:
        lines.insert(-2, line)
    lines[0] = "% RQ2 three-metric table. SQLancer/MonetDB is a branch-only local aggregate; unavailable metrics are shown as --."
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audit_note() -> None:
    audit = RESULT / "audit" / "sqlancer_monetdb_local_20260822"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "README.md").write_text(
        "# SQLancer/MonetDB local result\n\n"
        "The local run supplied the aggregate target-region branch count "
        "`7221.4` at 60 minutes and reported no later increase. The source "
        "did not include target-region or global-branch aggregate counts, so "
        "those table cells remain `--` and are not inferred.\n",
        encoding="utf-8",
    )


def main() -> None:
    backup_inputs()
    update_runs()
    update_summary()
    update_timeseries()
    update_stats()
    update_three_metric_csv()
    update_tex()
    write_audit_note()
    print(BACKUP)


if __name__ == "__main__":
    main()
