#!/usr/bin/env python3
"""Merge the valid PostgreSQL W/O-M1 replay into the RQ4 artifacts.

The replay intentionally contains four valid 24-hour repeats (r1, r2, r4,
r5).  The failed r3 repeat is not synthesized or counted as a completed run.
"""

from __future__ import annotations

import argparse
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
RESULT = RQ4 / "result"
DATA = RESULT / "data"
TABLES = RESULT / "tables"
TOOL = "SQLeek-W/O-M1"
DBMS = "PostgreSQL"
RUN_PREFIX = "postgresql_sqleek_wo_m1_"
RAW_PREFIXES = ("sqleek_wo_m1_postgres_", RUN_PREFIX)
VALID_REPEATS = {1, 2, 4, 5}
FROZEN_RISK_BRANCHES_TOTAL = 3200
FROZEN_RISK_TARGETS_TOTAL = 4845
FROZEN_GLOBAL_BRANCHES_TOTAL = 170620

DBMS_ORDER = {"PostgreSQL": 0, "MySQL": 1, "MariaDB": 2, "MonetDB": 3}
TOOL_ORDER = {
    "SQLeek-Full": 0,
    "SQLeek-W/O-M1": 1,
    "SQLeek-W/O-M2": 2,
    "SQLeek-W/O-M3": 3,
    "SQLeek-W/O-Risk-AwareDirected": 3,
}
COMPONENT_MAP = {
    "catalog_metadata": "catalog/metadata",
    "cursor_prepared": "cursor/prepared stmt",
    "type_system": "type system",
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


def read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def number(value: float) -> str:
    if not math.isfinite(value):
        return ""
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return f"{value:.15f}".rstrip("0").rstrip(".")


def repeat_id(row: dict[str, str]) -> int:
    return int(float(row.get("repeat_id", "0") or 0))


def canonical_run_id(row: dict[str, str]) -> str:
    return f"{RUN_PREFIX}r{repeat_id(row)}"


def canonical_row(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result["run_id"] = canonical_run_id(row)
    result["tool"] = TOOL
    result["dbms"] = DBMS
    return result


def normalized_coverage_row(
    row: dict[str, str], *, raw_region_total: float | None = None
) -> dict[str, str]:
    """Preserve replay ratios while using the RQ3 PG denominator map."""
    result = canonical_row(row)
    target_ratio = float(result["target_region_branch_coverage"])
    region_total = float(result.get("risk_targets_total") or raw_region_total or 0.0)
    region_ratio = float(result["risk_targets_hit"]) / region_total if region_total else 0.0
    global_ratio = float(result["global_branch_coverage"])
    result["risk_branches_total"] = str(FROZEN_RISK_BRANCHES_TOTAL)
    result["risk_branches_hit"] = number(target_ratio * FROZEN_RISK_BRANCHES_TOTAL)
    result["risk_targets_total"] = str(FROZEN_RISK_TARGETS_TOTAL)
    result["risk_targets_hit"] = number(region_ratio * FROZEN_RISK_TARGETS_TOTAL)
    result["global_branches_total"] = str(FROZEN_GLOBAL_BRANCHES_TOTAL)
    result["global_branches_hit"] = number(global_ratio * FROZEN_GLOBAL_BRANCHES_TOTAL)
    return result


def canonical_hit_row(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result["run_id"] = canonical_run_id(row)
    result["dbms"] = DBMS
    return result


def canonical_component_row(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result["tool"] = TOOL
    result["dbms"] = DBMS
    result["component"] = COMPONENT_MAP.get(
        result.get("component", ""), result.get("component", "")
    )
    if "repeat_id" in row and "run_id" in row:
        result["run_id"] = canonical_run_id(row)
    return result


def is_variant(row: dict[str, str]) -> bool:
    if row.get("dbms", "").strip().lower() not in {"postgres", "postgresql"}:
        return False
    if row.get("tool", "") in {TOOL, "SQLeek-RQ4-w-o-M1"}:
        return True
    run_id = row.get("run_id", "")
    return any(run_id.startswith(prefix) for prefix in RAW_PREFIXES)


def merge_csv(
    path: Path,
    new_rows: list[dict[str, str]],
    *,
    predicate=is_variant,
    sort_key=None,
) -> None:
    old_rows, old_fields = read_rows(path)
    kept = [row for row in old_rows if not predicate(row)]
    fields = list(old_fields)
    for row in new_rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    merged = kept + new_rows
    if sort_key is not None:
        merged.sort(key=sort_key)
    write_rows(path, merged, fields)


def variant_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        repeat_id(row),
        int(float(row.get("elapsed_min", "0") or 0)),
        row.get("run_id", ""),
    )


def summary_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        repeat_id(row),
        row.get("run_id", ""),
    )


def stats_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        int(float(row.get("elapsed_min", "0") or 0)),
    )


def build_stats(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[int(float(row["elapsed_min"]))].append(row)

    output: list[dict[str, str]] = []
    for minute in sorted(grouped):
        bucket = grouped[minute]

        def values(field: str) -> list[float]:
            return [float(row[field]) for row in bucket]

        def avg(field: str) -> float:
            return mean(values(field))

        def spread(field: str) -> float:
            vals = values(field)
            return stdev(vals) if len(vals) > 1 else 0.0

        target_std = spread("target_region_branch_coverage")
        global_std = spread("global_branch_coverage")
        target_se = target_std / math.sqrt(len(bucket))
        global_se = global_std / math.sqrt(len(bucket))
        target_total = int(round(avg("risk_branches_total")))
        regions_total = int(round(avg("risk_targets_total")))
        global_total = int(round(avg("global_branches_total")))
        output.append(
            {
                "tool": TOOL,
                "dbms": DBMS,
                "elapsed_min": str(minute),
                "mean_target_region_branch_coverage": number(
                    avg("target_region_branch_coverage")
                ),
                "std_target_region_branch_coverage": number(target_std),
                "mean_risk_branches_hit": number(avg("risk_branches_hit")),
                "std_risk_branches_hit": number(spread("risk_branches_hit")),
                "mean_risk_targets_hit": number(avg("risk_targets_hit")),
                "std_risk_targets_hit": number(spread("risk_targets_hit")),
                "mean_global_branch_coverage": number(avg("global_branch_coverage")),
                "std_global_branch_coverage": number(global_std),
                "mean_global_branches_hit": number(avg("global_branches_hit")),
                "std_global_branches_hit": number(spread("global_branches_hit")),
                "risk_branches_total": str(target_total),
                "risk_targets_total": str(regions_total),
                "global_branches_total": str(global_total),
                "mean_target_region_hit_coverage": number(
                    avg("risk_targets_hit") / regions_total
                ),
                "se_target_region_branch_coverage": number(target_se),
                "ci95_target_region_branch_coverage": number(1.96 * target_se),
                "se_global_branch_coverage": number(global_se),
                "ci95_global_branch_coverage": number(1.96 * global_se),
            }
        )
    return output


def aggregate_metrics(summary_rows: list[dict[str, str]]) -> dict[str, str]:
    def avg(field: str) -> float:
        return mean(float(row[field]) for row in summary_rows)

    return {
        "tool": TOOL,
        "dbms": DBMS,
        "target_region_branches_hit_mean": number(avg("risk_branches_hit")),
        "target_region_branches_total": number(avg("risk_branches_total")),
        "target_region_branch_coverage_mean": number(
            avg("target_region_branch_coverage")
        ),
        "target_regions_hit_mean": number(avg("risk_targets_hit")),
        "target_regions_total": number(avg("risk_targets_total")),
        "target_region_hit_coverage_mean": number(
            avg("risk_targets_hit") / avg("risk_targets_total")
        ),
        "global_branches_hit_mean": number(avg("global_branches_hit")),
        "global_branches_total": number(avg("global_branches_total")),
        "global_branch_coverage_mean": number(avg("global_branch_coverage")),
    }


def write_tex(path: Path, rows: list[dict[str, str]], comment: str) -> None:
    ordered = sorted(
        rows,
        key=lambda row: (
            TOOL_ORDER.get(row.get("tool", ""), 99),
            DBMS_ORDER.get(row.get("dbms", ""), 99),
        ),
    )
    lines = [
        comment,
        r"\begin{tabular}{llrrr}",
        r"\toprule",
        "Tool & DBMS & Target br. cov. & Region hit rate & Global br. cov. \\",
        r"\midrule",
    ]
    for row in ordered:
        lines.append(
            f"{row['tool']} & {row['dbms']} & "
            f"{100.0 * float(row['target_region_branch_coverage_mean']):.1f}\\% & "
            f"{100.0 * float(row['target_region_hit_coverage_mean']):.1f}\\% & "
            f"{100.0 * float(row['global_branch_coverage_mean']):.1f}\\% \\\\" 
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def backup_outputs(tag: str) -> Path:
    backup = DATA / "backups" / f"rq4_pg_wo_m1_{tag}"
    backup.mkdir(parents=True, exist_ok=True)
    paths = [
        DATA / "coverage_summary.csv",
        DATA / "coverage_timeseries.csv",
        DATA / "coverage_timeseries_stats.csv",
        DATA / "runs.csv",
        DATA / "component_heatmap.csv",
        DATA / "component_heatmap_by_run.csv",
        DATA / "target_branch_hits.csv",
        DATA / "target_region_hits.csv",
        DATA / "rq4_sqleek_variant_metrics.csv",
        DATA / "rq4_ablation_interim_total.csv",
        TABLES / "rq4_ablation_interim_total.csv",
        TABLES / "rq4_ablation_interim_total.tex",
        TABLES / "rq4_sqleek_variant_metrics.tex",
    ]
    for path in paths:
        if path.exists():
            shutil.copy2(path, backup / path.name)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--backup-tag", default=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    args = parser.parse_args()
    replay = args.replay_dir.resolve()
    required = ["coverage_summary.csv", "coverage_timeseries.csv", "runs.csv"]
    for name in required:
        if not (replay / name).exists():
            raise SystemExit(f"missing replay input: {replay / name}")

    raw_summary, _ = read_rows(replay / "coverage_summary.csv")
    repeats = {repeat_id(row) for row in raw_summary}
    if repeats != VALID_REPEATS:
        raise SystemExit(f"expected valid repeats {sorted(VALID_REPEATS)}, got {sorted(repeats)}")

    raw_timeseries, _ = read_rows(replay / "coverage_timeseries.csv")
    raw_runs, _ = read_rows(replay / "runs.csv")
    raw_components, _ = read_rows(replay / "component_heatmap.csv")
    raw_components_by_run, _ = read_rows(replay / "component_heatmap_by_run.csv")
    raw_branch_hits, _ = read_rows(replay / "target_branch_hits.csv")
    raw_region_hits, _ = read_rows(replay / "target_region_hits.csv")

    raw_denominators = {
        "risk_branches_total": raw_summary[0]["risk_branches_total"],
        "risk_targets_total": raw_summary[0]["risk_targets_total"],
        "global_branches_total": raw_summary[0]["global_branches_total"],
    }
    summary = [normalized_coverage_row(row) for row in raw_summary]
    timeseries = [
        normalized_coverage_row(
            row, raw_region_total=float(raw_denominators["risk_targets_total"])
        )
        for row in raw_timeseries
    ]
    runs = [canonical_row(row) for row in raw_runs]
    components = [canonical_component_row(row) for row in raw_components]
    components_by_run = [canonical_component_row(row) for row in raw_components_by_run]
    branch_hits = [canonical_hit_row(row) for row in raw_branch_hits]
    region_hits = [canonical_hit_row(row) for row in raw_region_hits]

    backup = backup_outputs(args.backup_tag)
    merge_csv(DATA / "coverage_timeseries.csv", timeseries, sort_key=variant_sort_key)
    merge_csv(DATA / "coverage_summary.csv", summary, sort_key=summary_sort_key)
    merge_csv(DATA / "runs.csv", runs, sort_key=summary_sort_key)
    merge_csv(DATA / "component_heatmap.csv", components, sort_key=variant_sort_key)
    merge_csv(DATA / "component_heatmap_by_run.csv", components_by_run)
    merge_csv(DATA / "target_branch_hits.csv", branch_hits)
    merge_csv(DATA / "target_region_hits.csv", region_hits)

    stats = build_stats(timeseries)
    merge_csv(DATA / "coverage_timeseries_stats.csv", stats, sort_key=stats_sort_key)

    metric_row = aggregate_metrics(summary)
    merge_csv(DATA / "rq4_sqleek_variant_metrics.csv", [metric_row], sort_key=summary_sort_key)

    table_path = TABLES / "rq4_ablation_interim_total.csv"
    table_rows, table_fields = read_rows(table_path)
    if not table_rows:
        table_rows, table_fields = read_rows(DATA / "rq4_ablation_interim_total.csv")
    table_row = dict(metric_row)
    table_row.update(
        {
            "status": "available_n4_partial",
            "source_package": str(replay),
            "note": (
                "formal PostgreSQL W/O-M1 replay; valid repeats r1,r2,r4,r5 (n=4); "
                "r3 excluded after early inode-exhaustion failure; "
                "60-1440 min checkpoints; final table value at 1440 min; "
                "coverage ratios preserved; branch totals use the RQ3 reference "
                "denominator map 3200/4845/170620"
            ),
        }
    )
    table_rows = [row for row in table_rows if not is_variant(row)] + [table_row]
    table_fields = list(table_fields)
    for field in table_row:
        if field not in table_fields:
            table_fields.append(field)
    table_rows.sort(key=lambda row: (TOOL_ORDER.get(row.get("tool", ""), 99), DBMS_ORDER.get(row.get("dbms", ""), 99)))
    write_rows(table_path, table_rows, table_fields)
    write_rows(DATA / "rq4_ablation_interim_total.csv", table_rows, table_fields)

    metrics_rows, metrics_fields = read_rows(DATA / "rq4_sqleek_variant_metrics.csv")
    write_rows(TABLES / "rq4_sqleek_variant_metrics.csv", metrics_rows, metrics_fields)
    write_tex(
        TABLES / "rq4_ablation_interim_total.tex",
        table_rows,
        "% Auto-generated RQ4 ablation table; PostgreSQL W/O-M1 currently has n=4 valid repeats.",
    )
    write_tex(
        TABLES / "rq4_sqleek_variant_metrics.tex",
        metrics_rows,
        "% Auto-generated RQ4 SQLeek variant metrics; PostgreSQL W/O-M1 currently has n=4 valid repeats.",
    )

    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation": "import_formal_postgresql_wo_m1_coverage_replay",
        "variant": TOOL,
        "dbms": DBMS,
        "source_package": str(replay),
        "valid_repeats": sorted(repeats),
        "excluded_repeats": {"3": "early inode-exhaustion failure"},
        "checkpoints_min": sorted({int(float(row["elapsed_min"])) for row in timeseries}),
        "raw_denominators": raw_denominators,
        "frozen_denominators": {
            "risk_branches_total": FROZEN_RISK_BRANCHES_TOTAL,
            "risk_targets_total": FROZEN_RISK_TARGETS_TOTAL,
            "global_branches_total": FROZEN_GLOBAL_BRANCHES_TOTAL,
        },
        "normalization": "coverage ratios preserved; branch totals use the RQ3 reference denominator map",
        "backup": str(backup),
        "status": "partial_n4_not_final_n5",
        "rows_added": {
            "coverage_summary": len(summary),
            "coverage_timeseries": len(timeseries),
            "coverage_timeseries_stats": len(stats),
            "runs": len(runs),
            "component_heatmap": len(components),
        },
    }
    provenance_path = DATA / f"rq4_wo_m1_postgres_replay_import_{args.backup_tag}.json"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance, indent=2))
    print(f"final_metrics={metric_row}")


if __name__ == "__main__":
    main()
