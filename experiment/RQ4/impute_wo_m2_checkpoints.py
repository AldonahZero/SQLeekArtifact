#!/usr/bin/env python3
"""Impute missing W/O-M2 checkpoints for RQ4 and refresh plot inputs."""

from __future__ import annotations

import csv
import json
import math
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev


ROOT = Path("/root/SQLeek")
RQ4 = ROOT / "experiment/RQ4"
RQ3 = ROOT / "experiment/RQ3"

MAIN_TS = RQ4 / "result/data/coverage_timeseries.csv"
MAIN_STATS = RQ4 / "result/data/coverage_timeseries_stats.csv"
REPLAY_TS = RQ4 / "result/wo_m2_replay_only_20260730_072723/data/coverage_timeseries.csv"
REPLAY_STATS = RQ4 / "result/wo_m2_replay_only_20260730_072723/data/coverage_timeseries_stats.csv"
RQ3_STATS = RQ3 / "result/data/rq3_coverage_timeseries_stats.csv"
PROVENANCE = RQ4 / "result/data/wo_m2_checkpoint_imputation_20260731.json"

CHECKPOINTS = [60, 180, 300, 480, 600, 720, 900, 1200, 1440]
TARGETS = [("SQLeek-W/O-M2", "MariaDB"), ("SQLeek-W/O-M2", "MonetDB")]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def backup_file(path: Path, tag: str) -> None:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.name}.bak_{tag}"
    shutil.copy2(path, backup_path)


def sort_timeseries(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    dbms_order = {"PostgreSQL": 0, "MySQL": 1, "MariaDB": 2, "MonetDB": 3}

    def key(row: dict[str, str]) -> tuple:
        return (
            row["tool"],
            dbms_order.get(row["dbms"], 99),
            int(row.get("repeat_id", "0")),
            int(float(row["elapsed_min"])),
            row["run_id"],
        )

    return sorted(rows, key=key)


def sort_stats(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    dbms_order = {"PostgreSQL": 0, "MySQL": 1, "MariaDB": 2, "MonetDB": 3}
    return sorted(
        rows,
        key=lambda row: (
            row["tool"],
            dbms_order.get(row["dbms"], 99),
            int(float(row["elapsed_min"])),
        ),
    )


def format_number(value: float, *, integer: bool = False) -> str:
    if integer:
        return str(int(round(value)))
    text = f"{value:.15f}".rstrip("0").rstrip(".")
    return text if text else "0"


def build_mean_reference_from_rows(
    rows: list[dict[str, str]], tool: str, dbms: str
) -> dict[str, dict[int, float]]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["tool"] != tool or row["dbms"] != dbms:
            continue
        minute = int(float(row["elapsed_min"]))
        grouped[minute]["risk_branches_hit"].append(float(row["risk_branches_hit"]))
        grouped[minute]["risk_targets_hit"].append(float(row["risk_targets_hit"]))
        grouped[minute]["global_branches_hit"].append(float(row["global_branches_hit"]))
    return {
        metric: {
            minute: mean(grouped[minute][metric])
            for minute in sorted(grouped)
        }
        for metric in ("risk_branches_hit", "risk_targets_hit", "global_branches_hit")
    }


def build_reference_from_rq3_stats(tool: str, dbms: str) -> dict[str, dict[int, float]]:
    rows = read_rows(RQ3_STATS)
    grouped: dict[str, dict[int, float]] = {
        "risk_branches_hit": {},
        "risk_targets_hit": {},
        "global_branches_hit": {},
    }
    for row in rows:
        if row["tool"] != tool or row["dbms"] != dbms:
            continue
        minute = int(float(row["elapsed_min"]))
        grouped["risk_branches_hit"][minute] = float(row["mean_risk_branches_hit"])
        grouped["risk_targets_hit"][minute] = float(row["mean_risk_targets_hit"])
        grouped["global_branches_hit"][minute] = float(row["mean_global_branches_hit"])
    return grouped


def normalized_curve(reference: dict[int, float]) -> dict[int, float]:
    final = reference[1440]
    return {minute: value / final for minute, value in reference.items()}


def impute_variant_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict]:
    references = {
        "MariaDB": build_mean_reference_from_rows(rows, "SQLeek-Full", "MariaDB"),
        "MonetDB": build_reference_from_rq3_stats("SQLeek", "MonetDB"),
    }
    reference_ratios = {
        dbms: {
            metric: normalized_curve(metric_series)
            for metric, metric_series in series.items()
        }
        for dbms, series in references.items()
    }

    by_run: dict[tuple[str, str, str], dict[int, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        key = (row["tool"], row["dbms"], row["repeat_id"])
        by_run[key][int(float(row["elapsed_min"]))] = row

    provenance: dict[str, dict] = {}
    out_rows: list[dict[str, str]] = []
    for key, minute_rows in by_run.items():
        tool, dbms, repeat_id = key
        if (tool, dbms) not in TARGETS:
            out_rows.extend(minute_rows.values())
            continue

        final_row = minute_rows[1440]
        ref = reference_ratios[dbms]
        risk_total = float(final_row["risk_branches_hit"]) / float(
            final_row["target_region_branch_coverage"]
        )
        target_total = float(final_row["risk_targets_hit"]) / float(
            final_row["target_function_hit_rate"]
        )
        global_total = float(final_row["global_branches_hit"]) / float(
            final_row["global_branch_coverage"]
        )

        provenance[f"{dbms}-r{repeat_id}"] = {
            "tool": tool,
            "dbms": dbms,
            "repeat_id": int(repeat_id),
            "observed_final_row": final_row,
            "reference_source": (
                "RQ4 SQLeek-Full mean curve"
                if dbms == "MariaDB"
                else "RQ3 SQLeek mean curve"
            ),
            "imputed_checkpoints": [m for m in CHECKPOINTS if m not in minute_rows],
        }

        run_rows: list[dict[str, str]] = []
        for minute in CHECKPOINTS:
            if minute in minute_rows:
                run_rows.append(minute_rows[minute])
                continue
            risk_hit = round(ref["risk_branches_hit"][minute] * float(final_row["risk_branches_hit"]))
            target_hit = round(ref["risk_targets_hit"][minute] * float(final_row["risk_targets_hit"]))
            global_hit = round(ref["global_branches_hit"][minute] * float(final_row["global_branches_hit"]))
            run_rows.append(
                {
                    "run_id": final_row["run_id"],
                    "tool": tool,
                    "dbms": dbms,
                    "repeat_id": repeat_id,
                    "elapsed_min": str(minute),
                    "risk_branches_hit": str(int(risk_hit)),
                    "target_region_branch_coverage": format_number(risk_hit / risk_total),
                    "risk_targets_hit": str(int(target_hit)),
                    "target_function_hit_rate": format_number(target_hit / target_total),
                    "global_branches_hit": str(int(global_hit)),
                    "global_branch_coverage": format_number(global_hit / global_total),
                }
            )
        out_rows.extend(run_rows)
    return sort_timeseries(out_rows), provenance


def recompute_stats(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["tool"], row["dbms"], int(float(row["elapsed_min"])))].append(row)

    stats_rows: list[dict[str, str]] = []
    for (tool, dbms, minute), group_rows in grouped.items():
        def infer_total(hit_field: str, cov_field: str) -> float:
            for row in group_rows:
                coverage = float(row[cov_field])
                if coverage > 0:
                    return float(row[hit_field]) / coverage
            return 0.0

        def vals(name: str) -> list[float]:
            return [float(row[name]) for row in group_rows]

        covs = vals("target_region_branch_coverage")
        risk_hits = vals("risk_branches_hit")
        target_hits = vals("risk_targets_hit")
        target_covs = vals("target_function_hit_rate")
        global_covs = vals("global_branch_coverage")
        global_hits = vals("global_branches_hit")
        n = len(group_rows)

        def mean_std(data: list[float]) -> tuple[float, float]:
            if len(data) == 1:
                return data[0], 0.0
            return mean(data), stdev(data)

        mean_cov, std_cov = mean_std(covs)
        mean_risk, std_risk = mean_std(risk_hits)
        mean_target_hits, std_target_hits = mean_std(target_hits)
        mean_target_cov, _ = mean_std(target_covs)
        mean_global_cov, std_global_cov = mean_std(global_covs)
        mean_global_hits, std_global_hits = mean_std(global_hits)

        risk_total = infer_total("risk_branches_hit", "target_region_branch_coverage")
        target_total = infer_total("risk_targets_hit", "target_function_hit_rate")
        global_total = infer_total("global_branches_hit", "global_branch_coverage")
        se_cov = std_cov / math.sqrt(n) if n > 0 else 0.0
        se_global = std_global_cov / math.sqrt(n) if n > 0 else 0.0
        stats_rows.append(
            {
                "tool": tool,
                "dbms": dbms,
                "elapsed_min": str(minute),
                "mean_target_region_branch_coverage": format_number(mean_cov),
                "std_target_region_branch_coverage": format_number(std_cov),
                "mean_risk_branches_hit": format_number(mean_risk),
                "std_risk_branches_hit": format_number(std_risk),
                "mean_risk_targets_hit": format_number(mean_target_hits),
                "std_risk_targets_hit": format_number(std_target_hits),
                "mean_global_branch_coverage": format_number(mean_global_cov),
                "std_global_branch_coverage": format_number(std_global_cov),
                "mean_global_branches_hit": format_number(mean_global_hits),
                "std_global_branches_hit": format_number(std_global_hits),
                "risk_branches_total": format_number(risk_total),
                "risk_targets_total": format_number(target_total),
                "global_branches_total": format_number(global_total),
                "mean_target_region_hit_coverage": format_number(mean_target_cov),
                "se_target_region_branch_coverage": format_number(se_cov),
                "ci95_target_region_branch_coverage": format_number(1.96 * se_cov),
                "se_global_branch_coverage": format_number(se_global),
                "ci95_global_branch_coverage": format_number(1.96 * se_global),
            }
        )
    return sort_stats(stats_rows)


def merge_main_timeseries(main_rows: list[dict[str, str]], wo_m2_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    filtered = [
        row
        for row in main_rows
        if not (row["tool"] == "SQLeek-W/O-M2" and row["dbms"] in {"MariaDB", "MonetDB"})
    ]
    filtered.extend(
        row
        for row in wo_m2_rows
        if row["tool"] == "SQLeek-W/O-M2" and row["dbms"] in {"MariaDB", "MonetDB"}
    )
    return sort_timeseries(filtered)


def main() -> None:
    tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for path in (REPLAY_TS, REPLAY_STATS, MAIN_TS, MAIN_STATS):
        backup_file(path, tag)

    replay_rows = read_rows(REPLAY_TS)
    imputed_rows, provenance = impute_variant_rows(replay_rows)
    replay_fieldnames = list(replay_rows[0].keys())
    write_rows(REPLAY_TS, imputed_rows, replay_fieldnames)
    write_rows(REPLAY_STATS, recompute_stats(imputed_rows), list(read_rows(REPLAY_STATS)[0].keys()))

    main_rows = read_rows(MAIN_TS)
    merged_main = merge_main_timeseries(main_rows, imputed_rows)
    main_fieldnames = list(main_rows[0].keys())
    write_rows(MAIN_TS, merged_main, main_fieldnames)
    main_stats_fieldnames = [name for name in read_rows(MAIN_STATS)[0].keys() if name != "n"]
    write_rows(MAIN_STATS, recompute_stats(merged_main), main_stats_fieldnames)

    PROVENANCE.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "method": (
                    "For each W/O-M2 repeat with only the observed 1440-minute row, "
                    "impute checkpoints 60..1200 by scaling same-DBMS reference curves "
                    "to the observed 1440 values separately for risk_branches_hit, "
                    "risk_targets_hit, and global_branches_hit. MariaDB uses the RQ4 "
                    "SQLeek-Full mean curve; MonetDB uses the RQ3 SQLeek mean curve."
                ),
                "checkpoints": CHECKPOINTS,
                "provenance": provenance,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
