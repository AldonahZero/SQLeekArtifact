#!/usr/bin/env python3
"""Import a formal W/O-M1 replay and refresh RQ4 artifacts.

The script is intentionally idempotent: rows belonging to the canonical
``SQLeek-W/O-M1`` variant are removed before the replay package is added.
Existing RQ4 variants are left untouched. MySQL follows the RQ4 branch-
denominator policy: replay ratios are retained and branch totals use the RQ3
reference map.
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
REPLAY = RQ4 / "live/rq4_wo_m1_mariadb_coverage_replay_20260801_172000/data"

TOOL = "SQLeek-W/O-M1"
SOURCE_TOOL = "SQLeek-RQ4-w-o-M1"
DBMS = "MariaDB"
RAW_RUN_PREFIX = "mariadb_sqleek-rq4-w-o-m1_"
CANONICAL_RUN_PREFIX = "mariadb_sqleek_wo_m1_"
REPLAY_IMAGE = ""
NORMALIZE_BRANCH_DENOMINATORS = False
RAW_DENOMINATORS: dict[str, int] = {}
BACKUP_PATH = ""

RISK_BRANCHES_TOTAL = 4248
RISK_TARGETS_TOTAL = 3041
GLOBAL_BRANCHES_TOTAL = 295012

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


def canonical_run_id(row: dict[str, str]) -> str:
    return f"{CANONICAL_RUN_PREFIX}r{int(float(row['repeat_id']))}"


def canonical_variant_row(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result["run_id"] = canonical_run_id(row)
    result["tool"] = TOOL
    result["dbms"] = DBMS
    return result


def normalized_coverage_row(row: dict[str, str]) -> dict[str, str]:
    """Canonicalize a coverage row and apply the frozen-denominator policy."""
    result = canonical_variant_row(row)
    if NORMALIZE_BRANCH_DENOMINATORS:
        target_ratio = float(result["target_region_branch_coverage"])
        global_ratio = float(result["global_branch_coverage"])
        result["risk_branches_total"] = str(RISK_BRANCHES_TOTAL)
        result["risk_branches_hit"] = number(target_ratio * RISK_BRANCHES_TOTAL)
        result["global_branches_total"] = str(GLOBAL_BRANCHES_TOTAL)
        result["global_branches_hit"] = number(global_ratio * GLOBAL_BRANCHES_TOTAL)
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
    if "repeat_id" in row and "run_id" in row:
        result["run_id"] = canonical_run_id(row)
    result["component"] = COMPONENT_MAP.get(result["component"], result["component"])
    return result


def is_wo_m1_row(row: dict[str, str]) -> bool:
    tool = row.get("tool", "")
    run_id = row.get("run_id", "")
    # A W/O-M1 import must only replace the same DBMS.  Without this guard,
    # importing MySQL would delete the existing MariaDB W/O-M1 rows because
    # both variants share the same tool label.
    if row.get("dbms", "").strip().lower() != DBMS.strip().lower():
        return False
    return (
        tool in {TOOL, SOURCE_TOOL}
        or (RAW_RUN_PREFIX and run_id.startswith(RAW_RUN_PREFIX))
        or run_id.startswith(CANONICAL_RUN_PREFIX)
    )


def merge_csv(
    path: Path,
    new_rows: list[dict[str, str]],
    *,
    sort_key=None,
) -> int:
    old_rows, old_fields = read_rows(path)
    kept = [row for row in old_rows if not is_wo_m1_row(row)]
    fields = list(old_fields)
    for row in new_rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    merged = kept + new_rows
    if sort_key is not None:
        merged.sort(key=sort_key)
    write_rows(path, merged, fields)
    return len(new_rows)


def variant_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        int(float(row.get("repeat_id", "0"))),
        int(float(row.get("elapsed_min", "0"))),
        row.get("run_id", ""),
    )


def summary_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        int(float(row.get("repeat_id", "0"))),
        row.get("run_id", ""),
    )


def stats_sort_key(row: dict[str, str]) -> tuple:
    return (
        TOOL_ORDER.get(row.get("tool", ""), 99),
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        int(float(row.get("elapsed_min", "0"))),
    )


def component_sort_key(row: dict[str, str]) -> tuple:
    return (
        DBMS_ORDER.get(row.get("dbms", ""), 99),
        COMPONENT_ORDER.index(row.get("component", ""))
        if row.get("component", "") in COMPONENT_ORDER
        else 99,
        TOOL_ORDER.get(row.get("tool", ""), 99),
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
                "risk_branches_total": str(RISK_BRANCHES_TOTAL),
                "risk_targets_total": str(RISK_TARGETS_TOTAL),
                "global_branches_total": str(GLOBAL_BRANCHES_TOTAL),
                "mean_target_region_hit_coverage": number(
                    avg("risk_targets_hit") / RISK_TARGETS_TOTAL
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
        "target_region_branches_total": str(RISK_BRANCHES_TOTAL),
        "target_region_branch_coverage_mean": number(
            avg("target_region_branch_coverage")
        ),
        "target_regions_hit_mean": number(avg("risk_targets_hit")),
        "target_regions_total": str(RISK_TARGETS_TOTAL),
        "target_region_hit_coverage_mean": number(
            avg("risk_targets_hit") / RISK_TARGETS_TOTAL
        ),
        "global_branches_hit_mean": number(avg("global_branches_hit")),
        "global_branches_total": str(GLOBAL_BRANCHES_TOTAL),
        "global_branch_coverage_mean": number(avg("global_branch_coverage")),
    }


def tex_rows(rows: list[dict[str, str]], comment: str) -> str:
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
        "Tool & DBMS & Target br. cov. & Region hit rate & Global br. cov. \\\\",
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
    return "\n".join(lines)


def write_tex(path: Path, rows: list[dict[str, str]], comment: str) -> None:
    path.write_text(tex_rows(rows, comment), encoding="utf-8")


def plot_component_heatmap(rows: list[dict[str, str]]) -> None:
    import matplotlib.pyplot as plt
    import numpy as np

    tools = ["SQLeek-Full", "SQLeek-W/O-M1", "SQLeek-W/O-M2", "SQLeek-W/O-M3"]
    display = {"SQLeek-W/O-Risk-AwareDirected": "SQLeek-W/O-M3"}
    values: dict[tuple[str, str, str], float] = {}
    for row in rows:
        tool = display.get(row.get("tool", ""), row.get("tool", ""))
        if tool not in tools:
            continue
        try:
            values[(row["dbms"], row["component"], tool)] = (
                100.0 * float(row["target_region_branch_coverage"])
            )
        except (KeyError, ValueError):
            continue

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.8), squeeze=False)
    image = None
    for axis, dbms in zip(axes.ravel(), DBMS_ORDER):
        matrix = np.array(
            [
                [values.get((dbms, component, tool), np.nan) for tool in tools]
                for component in COMPONENT_ORDER
            ],
            dtype=float,
        )
        masked = np.ma.masked_invalid(matrix)
        image = axis.imshow(masked, cmap="YlGnBu", vmin=0, vmax=100, aspect="auto")
        axis.set_title(dbms, fontweight="bold", fontsize=9)
        axis.set_xticks(range(len(tools)), ["Full", "W/O-M1", "W/O-M2", "W/O-M3"])
        axis.set_yticks(range(len(COMPONENT_ORDER)), COMPONENT_ORDER)
        axis.tick_params(axis="x", labelrotation=35, labelsize=7)
        axis.tick_params(axis="y", labelsize=7)
        for i in range(len(COMPONENT_ORDER)):
            for j in range(len(tools)):
                value = matrix[i, j]
                label = "—" if np.isnan(value) else f"{value:.1f}"
                axis.text(j, i, label, ha="center", va="center", fontsize=6.5)
        axis.set_xticks(np.arange(-0.5, len(tools), 1), minor=True)
        axis.set_yticks(np.arange(-0.5, len(COMPONENT_ORDER), 1), minor=True)
        axis.grid(which="minor", color="white", linewidth=0.8)
        axis.tick_params(which="minor", bottom=False, left=False)

    fig.subplots_adjust(left=0.10, right=0.84, bottom=0.09, top=0.91,
                        wspace=0.50, hspace=0.72)
    if image is not None:
        colorbar_axis = fig.add_axes([0.875, 0.25, 0.025, 0.50])
        fig.colorbar(image, cax=colorbar_axis,
                     label="Target-region branch coverage (%)")
    fig.suptitle("RQ4 component-level target-region branch coverage", y=0.975,
                 fontsize=10, fontweight="bold")
    out = RESULT / "figures/heatmaps"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "rq4_component_heatmap.png", dpi=300, bbox_inches="tight")
    fig.savefig(out / "rq4_component_heatmap.pdf", bbox_inches="tight")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dbms",
        choices=("mariadb", "mysql", "monetdb"),
        default="mariadb",
        help="DBMS represented by the replay package (default: mariadb).",
    )
    parser.add_argument(
        "--replay-dir",
        type=Path,
        help="Replay data directory containing coverage_summary.csv.",
    )
    parser.add_argument(
        "--coverage-image",
        default="",
        help="Coverage image tag or digest recorded in provenance.",
    )
    parser.add_argument(
        "--backup-path",
        default="",
        help="Backup directory recorded in provenance.",
    )
    return parser.parse_args()


def configure_variant(args: argparse.Namespace) -> None:
    global REPLAY, DBMS, RAW_RUN_PREFIX, CANONICAL_RUN_PREFIX
    global RISK_BRANCHES_TOTAL, RISK_TARGETS_TOTAL, GLOBAL_BRANCHES_TOTAL
    global REPLAY_IMAGE, NORMALIZE_BRANCH_DENOMINATORS, BACKUP_PATH

    if args.replay_dir:
        REPLAY = args.replay_dir
    REPLAY_IMAGE = args.coverage_image
    BACKUP_PATH = args.backup_path
    if args.dbms == "mysql":
        DBMS = "MySQL"
        # mysql_sqleek_rN is also used by the existing W/O-M2/W/O-M3 rows;
        # only the canonical W/O-M1 prefix is safe for replacement.
        RAW_RUN_PREFIX = ""
        CANONICAL_RUN_PREFIX = "mysql_sqleek_wo_m1_"
        RISK_BRANCHES_TOTAL = 4602
        RISK_TARGETS_TOTAL = 1445
        GLOBAL_BRANCHES_TOTAL = 326944
        NORMALIZE_BRANCH_DENOMINATORS = True
    elif args.dbms == "monetdb":
        DBMS = "MonetDB"
        RAW_RUN_PREFIX = "monetdb_sqleek_w_o_m1_"
        CANONICAL_RUN_PREFIX = "monetdb_sqleek_wo_m1_"
        RISK_BRANCHES_TOTAL = 120042
        RISK_TARGETS_TOTAL = 10233
        GLOBAL_BRANCHES_TOTAL = 301870
        NORMALIZE_BRANCH_DENOMINATORS = False
    else:
        DBMS = "MariaDB"
        RAW_RUN_PREFIX = "mariadb_sqleek-rq4-w-o-m1_"
        CANONICAL_RUN_PREFIX = "mariadb_sqleek_wo_m1_"
        RISK_BRANCHES_TOTAL = 4248
        RISK_TARGETS_TOTAL = 3041
        GLOBAL_BRANCHES_TOTAL = 295012
        NORMALIZE_BRANCH_DENOMINATORS = False


def infer_raw_denominators(summary: list[dict[str, str]]) -> dict[str, int]:
    if not summary:
        return {}
    row = summary[0]
    return {
        "target_region_branches": int(float(row["risk_branches_total"])),
        "target_regions": int(float(row["risk_targets_total"])),
        "global_branches": int(float(row["global_branches_total"])),
    }


def main() -> None:
    args = parse_args()
    configure_variant(args)
    if not REPLAY.is_dir():
        raise SystemExit(f"missing replay data directory: {REPLAY}")
    required = ["coverage_summary.csv", "coverage_timeseries.csv", "runs.csv"]
    for name in required:
        if not (REPLAY / name).exists():
            raise SystemExit(f"missing replay input: {REPLAY / name}")

    raw_summary, _ = read_rows(REPLAY / "coverage_summary.csv")
    raw_timeseries, _ = read_rows(REPLAY / "coverage_timeseries.csv")
    raw_runs, _ = read_rows(REPLAY / "runs.csv")
    raw_components, _ = read_rows(REPLAY / "component_heatmap.csv")
    raw_components_by_run, _ = read_rows(REPLAY / "component_heatmap_by_run.csv")
    raw_branch_hits, _ = read_rows(REPLAY / "target_branch_hits.csv")
    raw_region_hits, _ = read_rows(REPLAY / "target_region_hits.csv")

    RAW_DENOMINATORS.clear()
    RAW_DENOMINATORS.update(infer_raw_denominators(raw_summary))
    summary = [normalized_coverage_row(row) for row in raw_summary]
    timeseries = [normalized_coverage_row(row) for row in raw_timeseries]
    runs = [canonical_variant_row(row) for row in raw_runs]
    components = [canonical_component_row(row) for row in raw_components]
    components_by_run = [canonical_component_row(row) for row in raw_components_by_run]
    branch_hits = [canonical_hit_row(row) for row in raw_branch_hits]
    region_hits = [canonical_hit_row(row) for row in raw_region_hits]

    merge_csv(DATA / "coverage_timeseries.csv", timeseries, sort_key=variant_sort_key)
    merge_csv(DATA / "coverage_summary.csv", summary, sort_key=summary_sort_key)
    merge_csv(DATA / "runs.csv", runs, sort_key=summary_sort_key)
    merge_csv(DATA / "component_heatmap.csv", components, sort_key=component_sort_key)
    merge_csv(DATA / "component_heatmap_by_run.csv", components_by_run)
    merge_csv(DATA / "target_branch_hits.csv", branch_hits)
    merge_csv(DATA / "target_region_hits.csv", region_hits)

    target_regions = REPLAY / "target_regions.csv"
    target_regions_output = DATA / f"target_regions_{DBMS.lower()}.csv"
    if target_regions.exists():
        # Keep the historical generic file stable (it was originally the
        # MariaDB target map), while retaining a DBMS-specific copy for every
        # imported replay package.
        shutil.copy2(target_regions, target_regions_output)
        if not (DATA / "target_regions.csv").exists():
            shutil.copy2(target_regions, DATA / "target_regions.csv")

    new_stats = build_stats(timeseries)
    merge_csv(DATA / "coverage_timeseries_stats.csv", new_stats, sort_key=stats_sort_key)

    metrics, metric_fields = read_rows(DATA / "rq4_sqleek_variant_metrics.csv")
    metric_row = aggregate_metrics(summary)
    merge_csv(DATA / "rq4_sqleek_variant_metrics.csv", [metric_row], sort_key=summary_sort_key)

    table_path = TABLES / "rq4_ablation_interim_total.csv"
    table_rows, _ = read_rows(table_path)
    if not table_rows:
        table_rows, _ = read_rows(DATA / "rq4_ablation_interim_total.csv")
    table_row = dict(metric_row)
    table_row.update(
        {
            "status": "available",
            "source_package": str(REPLAY),
            "note": (
                "formal MariaDB W/O-M1 replay; 5 repeats; 0-1440 min checkpoints; "
                "RQ3 reference denominator map"
                if DBMS == "MariaDB"
                else (
                    "formal MonetDB W/O-M1 LLVM replay; 5 repeats; 60-1440 min "
                    "checkpoints; RQ3 reference denominator map"
                    if DBMS == "MonetDB"
                    else (
                        f"formal MySQL W/O-M1 replay; 5 repeats; 60-1440 min checkpoints; "
                        f"coverage ratios preserved; branch totals use the RQ3 "
                        f"reference denominator map "
                        f"{RISK_BRANCHES_TOTAL}/{GLOBAL_BRANCHES_TOTAL}"
                    )
                )
            ),
        }
    )
    table_fields = [
        "tool", "dbms", "target_region_branches_hit_mean",
        "target_region_branches_total", "target_region_branch_coverage_mean",
        "target_regions_hit_mean", "target_regions_total",
        "target_region_hit_coverage_mean", "global_branches_hit_mean",
        "global_branches_total", "global_branch_coverage_mean", "status",
        "source_package", "note",
    ]
    old_table_fields = list(table_fields)
    if table_rows:
        old_table_fields = list(read_rows(table_path)[1] or table_fields)
    merged_table = [row for row in table_rows if not is_wo_m1_row(row)] + [table_row]
    for field in table_fields:
        if field not in old_table_fields:
            old_table_fields.append(field)
    merged_table.sort(
        key=lambda row: (
            TOOL_ORDER.get(row.get("tool", ""), 99),
            DBMS_ORDER.get(row.get("dbms", ""), 99),
        )
    )
    write_rows(table_path, merged_table, old_table_fields)
    write_rows(DATA / "rq4_ablation_interim_total.csv", merged_table, old_table_fields)

    metrics_rows, metrics_fields = read_rows(DATA / "rq4_sqleek_variant_metrics.csv")
    write_tex(
        TABLES / "rq4_ablation_interim_total.tex",
        merged_table,
        "% Auto-generated RQ4 ablation total table (W/O-M1 replay added; PostgreSQL W/O-M2 r1 excluded).",
    )
    write_tex(
        TABLES / "rq4_sqleek_variant_metrics.tex",
        metrics_rows,
        "% Auto-generated RQ4 SQLeek variant metrics (W/O-M1 replay added).",
    )
    if (TABLES / "rq4_sqleek_variant_metrics.csv").exists():
        write_rows(TABLES / "rq4_sqleek_variant_metrics.csv", metrics_rows, metrics_fields)

    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation": f"import_formal_{DBMS.lower()}_wo_m1_coverage_replay",
        "variant": TOOL,
        "dbms": DBMS,
        "source_package": str(REPLAY),
        "coverage_image": REPLAY_IMAGE,
        "repeats": len(summary),
        "checkpoints_per_repeat": len(timeseries) // len(summary),
        "checkpoints_min": sorted({int(float(row["elapsed_min"])) for row in timeseries}),
        "denominators": {
            "target_region_branches": RISK_BRANCHES_TOTAL,
            "target_regions": RISK_TARGETS_TOTAL,
            "global_branches": GLOBAL_BRANCHES_TOTAL,
        },
        "raw_denominators": RAW_DENOMINATORS,
        "normalization": (
            "none"
            if not NORMALIZE_BRANCH_DENOMINATORS
            else "coverage ratios preserved; branch totals use the RQ3 reference denominator map"
        ),
        "target_region_source": str(
            ROOT / "experiment/RQ3/result/audit/sqleek_mysql/target_regions.csv"
        ) if DBMS == "MySQL" else str(target_regions_output),
        "backup": BACKUP_PATH,
        "rows_added": {
            "coverage_summary": len(summary),
            "coverage_timeseries": len(timeseries),
            "coverage_timeseries_stats": len(new_stats),
            "runs": len(runs),
            "component_heatmap": len(components),
        },
    }
    provenance_name = {
        "MySQL": "rq4_wo_m1_mysql_replay_import_20260802.json",
        "MariaDB": "rq4_wo_m1_replay_import_20260802.json",
        "MonetDB": "rq4_wo_m1_monetdb_replay_import_20260805.json",
    }[DBMS]
    (DATA / provenance_name).write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

    plot_component_heatmap(
        [
            *read_rows(DATA / "component_heatmap.csv")[0],
        ]
    )

    print(json.dumps(provenance, indent=2))
    print(f"final_metrics={metric_row}")


if __name__ == "__main__":
    main()
