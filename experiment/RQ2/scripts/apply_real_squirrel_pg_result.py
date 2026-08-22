#!/usr/bin/env python3
"""Merge real replay rows into the existing RQ2 result package.

The script updates one or more fulfilled (tool, DBMS) pairs under
``/root/SQLeek/experiment/RQ2/result/`` and regenerates the result tables and
figures from the current real-result CSV files.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


BASE_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = BASE_DIR / "result"
DATA_DIR = RESULT_DIR / "data"
AUDIT_DIR = RESULT_DIR / "audit" / "squirrel_postgres"

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from figure_paths import (  # noqa: E402
    HEATMAP_DIR,
    HOUR24_DIR,
    cleanup_legacy_flat_figures,
    dbms_slug,
    ensure_fig_dirs,
    iter_generated_figures,
)

TOOLS = [
    "SQLeek",
    "SQLaser",
    "AFLGo",
    "Griffin",
    "SQUIRREL",
    "DynSQL",
    "SQLancer",
]
PLOT_TOOLS = ["SQLeek", "SQLaser", "AFLGo", "Griffin", "SQUIRREL", "DynSQL", "SQLancer"]
DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "SQLite", "MonetDB"]
PLOT_DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
COMPONENTS = [
    "parser",
    "optimizer",
    "executor",
    "type system",
    "catalog/metadata",
    "storage",
    "cursor/prepared stmt",
    "other",
]
TOOL_STYLES = {
    "SQLeek": {"color": "#d62728", "marker": "o"},
    "SQLaser": {"color": "#1f77b4", "marker": "s"},
    "AFLGo": {"color": "#2ca02c", "marker": "D"},
    "Griffin": {"color": "#ff7f0e", "marker": "^"},
    "SQUIRREL": {"color": "#9467bd", "marker": "v"},
    "DynSQL": {"color": "#8c564b", "marker": "X"},
    "SQLancer": {"color": "#7f7f7f", "marker": "x"},
}
UNSUPPORTED_TOOL_DBMS = {
}
COMPONENT_HEATMAP_OMIT_TOOL_DBMS: dict[tuple[str, str], str] = {
    ("SQLancer", "MonetDB"): "branch-only aggregate; component metrics were not supplied",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-data",
        type=Path,
        action="append",
        help="real replay data directory containing coverage_summary.csv; may be passed multiple times",
    )
    parser.add_argument("--tool", default="SQUIRREL", help="tool name for the real rows; defaults to SQUIRREL")
    parser.add_argument(
        "--merge-into-existing",
        action="store_true",
        help="compatibility flag; real rows are always merged into the existing result package",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="regenerate current result tables and figures without merging new rows",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def denominator_lock() -> dict[str, tuple[float, int, int]]:
    path = DATA_DIR / "coverage_denominator_lock.csv"
    rows = read_csv(path)
    if not rows:
        raise SystemExit(f"missing or empty denominator lock: {path}")
    return {
        norm_dbms(row["dbms"]): (
            float(row["risk_branches_total"]),
            int(row["risk_targets_total"]),
            int(row["global_branches_total"]),
        )
        for row in rows
    }


def validate_denominators(rows: list[dict[str, str]], context: str) -> None:
    lock = denominator_lock()
    errors = []
    for row in rows:
        dbms = norm_dbms(row.get("dbms", ""))
        expected = lock.get(dbms)
        if expected is None:
            continue
        try:
            observed = (
                float(row["risk_branches_total"]),
                int(float(row["risk_targets_total"])),
                int(float(row["global_branches_total"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{row.get('run_id', '<unknown>')}: invalid denominator fields ({exc})")
            continue
        if (
            not math.isclose(observed[0], expected[0], rel_tol=0.0, abs_tol=1e-9)
            or observed[1:] != expected[1:]
        ):
            errors.append(
                f"{row.get('run_id', '<unknown>')}: {dbms} denominator "
                f"{observed} != locked {expected}"
            )
    if errors:
        detail = "\n  - ".join(errors[:20])
        extra = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        raise SystemExit(f"{context} failed denominator validation:\n  - {detail}{extra}")


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def norm_dbms(value: str) -> str:
    mapping = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "sqlite": "SQLite",
        "monetdb": "MonetDB",
    }
    return mapping.get(value.strip().lower(), value)


def norm_component(value: str) -> str:
    mapping = {
        "type_system": "type system",
        "catalog_metadata": "catalog/metadata",
        "catalog": "catalog/metadata",
        "prepared": "cursor/prepared stmt",
        "cursor_prepared": "cursor/prepared stmt",
        "cursor/prepared": "cursor/prepared stmt",
    }
    return mapping.get(value.strip(), value.strip())


def tool_slug(tool: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", tool.strip().lower()).strip("_") or "tool"


def norm_run_id(row: dict[str, str], tool: str) -> str:
    repeat = row.get("repeat_id") or "1"
    dbms = norm_dbms(row.get("dbms", "PostgreSQL")).lower()
    if dbms == "postgresql":
        dbms = "postgresql"
    return f"{dbms}_{tool_slug(tool)}_r{repeat}"


def row_pair(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("tool", ""), norm_dbms(row.get("dbms", "")))


def unsupported_reason(row: dict[str, str]) -> str | None:
    return UNSUPPORTED_TOOL_DBMS.get((row.get("tool", ""), norm_dbms(row.get("dbms", ""))))


def apply_unsupported_pairs() -> None:
    runs_path = DATA_DIR / "runs.csv"
    run_rows = read_csv(runs_path)
    if run_rows:
        fields = list(run_rows[0].keys())
        updated = []
        for row in run_rows:
            row = dict(row)
            reason = unsupported_reason(row)
            if reason:
                row["status"] = "unsupported"
                row["unsupported_reason"] = reason
                row["version"] = ""
                row["seed_corpus"] = ""
                row["build_id"] = ""
                row["container_id"] = ""
            updated.append(row)
        write_csv(runs_path, updated, fields)

    for name in [
        "coverage_summary.csv",
        "coverage_timeseries.csv",
        "runtime_edges_found.csv",
        "component_heatmap.csv",
        "coverage_timeseries_stats.csv",
    ]:
        path = DATA_DIR / name
        rows = read_csv(path)
        if not rows:
            continue
        fields = list(rows[0].keys())
        write_csv(path, [row for row in rows if unsupported_reason(row) is None], fields)


def replace_rows(file_name: str, real_rows: list[dict[str, object]], sort_key) -> None:
    path = DATA_DIR / file_name
    existing = read_csv(path)
    fields = list(existing[0].keys()) if existing else list(real_rows[0].keys())
    pairs = {(str(row.get("tool", "")), norm_dbms(str(row.get("dbms", "")))) for row in real_rows}
    kept = [row for row in existing if row_pair(row) not in pairs]
    merged = kept + [{field: row.get(field, "") for field in fields} for row in real_rows]
    merged.sort(key=sort_key)
    write_csv(path, merged, fields)


def audit_dir_for(real_rows: list[dict[str, str]], tool: str) -> Path:
    dbms = norm_dbms(real_rows[0].get("dbms", "postgres")) if real_rows else "PostgreSQL"
    slug = dbms.lower().replace("postgresql", "postgres")
    return RESULT_DIR / "audit" / f"{tool_slug(tool)}_{slug}"


def load_real(real_data: Path, name: str) -> list[dict[str, str]]:
    path = real_data / name
    if not path.exists():
        raise SystemExit(f"missing real data file: {path}")
    return read_csv(path)


def merge_real_rows(real_data: Path, tool: str) -> None:
    real_summary_rows = load_real(real_data, "coverage_summary.csv")
    normalized_for_validation = []
    for source in real_summary_rows:
        row = dict(source)
        row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
        normalized_for_validation.append(row)
    validate_denominators(normalized_for_validation, f"replay input {real_data}")
    audit_dir = audit_dir_for(real_summary_rows, tool)
    audit_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    for row in load_real(real_data, "runs.csv"):
        row = dict(row)
        row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
        row["run_id"] = norm_run_id(row, tool)
        row["tool"] = tool
        row["version"] = row.get("version") or f"{row['dbms']} llvmcov"
        runs.append(row)
    replace_rows(
        "runs.csv",
        runs,
        lambda r: (DBMS_ORDER.index(norm_dbms(r["dbms"])) if norm_dbms(r["dbms"]) in DBMS_ORDER else 99, TOOLS.index(r["tool"]) if r["tool"] in TOOLS else 99, int(r["repeat_id"])),
    )

    summary = []
    for row in real_summary_rows:
        row = dict(row)
        row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
        row["run_id"] = norm_run_id(row, tool)
        row["tool"] = tool
        summary.append(row)
    replace_rows(
        "coverage_summary.csv",
        summary,
        lambda r: (DBMS_ORDER.index(norm_dbms(r["dbms"])) if norm_dbms(r["dbms"]) in DBMS_ORDER else 99, TOOLS.index(r["tool"]) if r["tool"] in TOOLS else 99, int(r["repeat_id"])),
    )

    timeseries = []
    for row in load_real(real_data, "coverage_timeseries.csv"):
        row = dict(row)
        row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
        row["run_id"] = norm_run_id(row, tool)
        row["tool"] = tool
        timeseries.append(row)
    replace_rows(
        "coverage_timeseries.csv",
        timeseries,
        lambda r: (
            DBMS_ORDER.index(norm_dbms(r["dbms"])) if norm_dbms(r["dbms"]) in DBMS_ORDER else 99,
            TOOLS.index(r["tool"]) if r["tool"] in TOOLS else 99,
            int(r["repeat_id"]),
            int(r["elapsed_min"]),
        ),
    )

    runtime = []
    runtime_path = real_data / "runtime_edges_found.csv"
    if runtime_path.exists():
        for row in read_csv(runtime_path):
            row = dict(row)
            row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
            row["run_id"] = norm_run_id(row, tool)
            row["tool"] = tool
            runtime.append(row)
        replace_rows(
            "runtime_edges_found.csv",
            runtime,
            lambda r: (
                DBMS_ORDER.index(norm_dbms(r["dbms"])) if norm_dbms(r["dbms"]) in DBMS_ORDER else 99,
                TOOLS.index(r["tool"]) if r["tool"] in TOOLS else 99,
                int(r["repeat_id"]),
                int(r["relative_time"]),
            ),
        )

    heatmap = []
    for row in load_real(real_data, "component_heatmap.csv"):
        row = dict(row)
        row["tool"] = tool
        row["dbms"] = norm_dbms(row.get("dbms", "PostgreSQL"))
        row["component"] = norm_component(row.get("component", ""))
        heatmap.append(row)
    replace_rows(
        "component_heatmap.csv",
        heatmap,
        lambda r: (
            COMPONENTS.index(norm_component(r["component"])) if norm_component(r["component"]) in COMPONENTS else 99,
            TOOLS.index(r["tool"]) if r["tool"] in TOOLS else 99,
            DBMS_ORDER.index(norm_dbms(r["dbms"])) if norm_dbms(r["dbms"]) in DBMS_ORDER else 99,
        ),
    )

    for name in [
        "component_heatmap_by_run.csv",
        "target_region_hits.csv",
        "target_branch_hits.csv",
    ]:
        src = real_data / name
        if src.exists():
            shutil.copy2(src, audit_dir / name)
    for name in ["target_regions.csv", "replay_index.tsv", "preflight_status.tsv"]:
        src = real_data / name
        if not src.exists():
            src = real_data.parent / name
        if src.exists():
            shutil.copy2(src, audit_dir / name)


def write_three_metric_tables() -> None:
    apply_unsupported_pairs()
    df = pd.read_csv(DATA_DIR / "coverage_summary.csv")
    numeric_cols = [
        "risk_branches_total",
        "risk_branches_hit",
        "target_region_branch_coverage",
        "risk_targets_total",
        "risk_targets_hit",
        "global_branches_total",
        "global_branches_hit",
        "global_branch_coverage",
    ]
    for col in numeric_cols:
        if col in df.columns:
            converted = pd.to_numeric(df[col], errors="coerce")
            if col not in {"risk_branches_hit", "risk_targets_hit", "global_branches_hit"}:
                converted = converted.fillna(0.0)
            df[col] = converted
    grouped = (
        df.groupby(["tool", "dbms"], as_index=False)
        .agg(
            n=("run_id", "count"),
            target_region_branches_hit_mean=("risk_branches_hit", "mean"),
            target_region_branches_total=("risk_branches_total", "max"),
            target_region_branch_coverage_mean=("target_region_branch_coverage", "mean"),
            target_regions_hit_mean=("risk_targets_hit", "mean"),
            target_regions_total=("risk_targets_total", "max"),
            global_branches_hit_mean=("global_branches_hit", "mean"),
            global_branches_total=("global_branches_total", "max"),
            global_branch_coverage_mean=("global_branch_coverage", "mean"),
        )
        .sort_values(
            ["dbms", "tool"],
            key=lambda s: s.map(
                {name: idx for idx, name in enumerate(DBMS_ORDER)}
                if s.name == "dbms"
                else {name: idx for idx, name in enumerate(TOOLS)}
            ).fillna(99),
        )
    )
    grouped["target_region_hit_coverage_mean"] = grouped.apply(
        lambda r: r["target_regions_hit_mean"] / r["target_regions_total"] if r["target_regions_total"] else 0.0,
        axis=1,
    )
    fields = [
        "tool",
        "dbms",
        "target_region_branches_hit_mean",
        "target_region_branches_total",
        "target_region_branch_coverage_mean",
        "target_regions_hit_mean",
        "target_regions_total",
        "target_region_hit_coverage_mean",
        "global_branches_hit_mean",
        "global_branches_total",
        "global_branch_coverage_mean",
    ]
    grouped[fields].to_csv(DATA_DIR / "rq2_three_metrics_by_tool_dbms.csv", index=False)

    def fmt_hit(value: float) -> str:
        value = float(value)
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.1f}"

    def metric_cell(hit: float, total: float, cov: float) -> str:
        if pd.isna(hit) or pd.isna(cov):
            return "--"
        total_int = int(total)
        if total_int <= 0:
            return "--"
        return f"{fmt_hit(hit)}/{total_int} ({cov * 100.0:.1f}\\%)"

    lines = [
        "% RQ2 three-metric table. SQLancer/MonetDB is a branch-only local aggregate; unavailable metrics are shown as --.",
        "\\begin{tabular}{llrrr}",
        "\\hline",
        "Tool & DBMS & Target-region branches & Target regions & Global branches \\\\",
        "\\hline",
    ]
    for row in grouped.itertuples(index=False):
        branch = metric_cell(
            row.target_region_branches_hit_mean,
            row.target_region_branches_total,
            row.target_region_branch_coverage_mean,
        )
        regions = metric_cell(
            row.target_regions_hit_mean,
            row.target_regions_total,
            row.target_region_hit_coverage_mean,
        )
        global_branch = metric_cell(
            row.global_branches_hit_mean,
            row.global_branches_total,
            row.global_branch_coverage_mean,
        )
        lines.append(f"{row.tool} & {row.dbms} & {branch} & {regions} & {global_branch} \\\\")
    lines.extend(["\\hline", "\\end{tabular}", ""])
    (DATA_DIR / "rq2_three_metrics_by_tool_dbms.tex").write_text("\n".join(lines), encoding="utf-8")


def write_timeseries_stats() -> None:
    apply_unsupported_pairs()
    df = pd.read_csv(DATA_DIR / "coverage_timeseries.csv")
    summary = pd.read_csv(DATA_DIR / "coverage_summary.csv")
    totals = (
        summary.groupby(["tool", "dbms"], as_index=False)
        .agg(
            risk_branches_total=("risk_branches_total", "max"),
            risk_targets_total=("risk_targets_total", "max"),
            global_branches_total=("global_branches_total", "max"),
        )
    )
    grouped = (
        df.groupby(["tool", "dbms", "elapsed_min"], as_index=False)
        .agg(
            n=("target_region_branch_coverage", "count"),
            mean_target_region_branch_coverage=("target_region_branch_coverage", "mean"),
            std_target_region_branch_coverage=("target_region_branch_coverage", "std"),
            mean_risk_branches_hit=("risk_branches_hit", "mean"),
            std_risk_branches_hit=("risk_branches_hit", "std"),
            mean_risk_targets_hit=("risk_targets_hit", "mean"),
            std_risk_targets_hit=("risk_targets_hit", "std"),
            mean_global_branch_coverage=("global_branch_coverage", "mean"),
            std_global_branch_coverage=("global_branch_coverage", "std"),
            mean_global_branches_hit=("global_branches_hit", "mean"),
            std_global_branches_hit=("global_branches_hit", "std"),
        )
        .sort_values(["dbms", "tool", "elapsed_min"])
    )
    grouped = grouped.merge(totals, on=["tool", "dbms"], how="left")
    grouped["mean_target_region_hit_coverage"] = grouped.apply(
        lambda r: r["mean_risk_targets_hit"] / r["risk_targets_total"] if r["risk_targets_total"] else 0.0,
        axis=1,
    )
    grouped = grouped.fillna(0.0)
    n_sqrt = np.sqrt(grouped["n"].clip(lower=1))
    grouped["se_target_region_branch_coverage"] = grouped["std_target_region_branch_coverage"] / n_sqrt
    grouped["ci95_target_region_branch_coverage"] = 1.96 * grouped["se_target_region_branch_coverage"]
    grouped["se_global_branch_coverage"] = grouped["std_global_branch_coverage"] / n_sqrt
    grouped["ci95_global_branch_coverage"] = 1.96 * grouped["se_global_branch_coverage"]
    grouped.drop(columns=["n"]).to_csv(DATA_DIR / "coverage_timeseries_stats.csv", index=False)


def pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = len(x)
    h = np.diff(x)
    delta = np.diff(y) / h
    m = np.zeros(n, dtype=float)
    if n == 2:
        m[0] = delta[0]
        m[1] = delta[0]
        return m
    m[0] = ((2 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
    if m[0] * delta[0] <= 0:
        m[0] = 0.0
    elif abs(m[0]) > 3 * abs(delta[0]):
        m[0] = 3 * delta[0]
    m[-1] = ((2 * h[-1] + h[-2]) * delta[-1] - h[-1] * delta[-2]) / (h[-1] + h[-2])
    if m[-1] * delta[-1] <= 0:
        m[-1] = 0.0
    elif abs(m[-1]) > 3 * abs(delta[-1]):
        m[-1] = 3 * delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return m


def pchip_interpolate(x: np.ndarray, y: np.ndarray, x_new: np.ndarray) -> np.ndarray:
    m = pchip_slopes(x, y)
    y_new = np.empty_like(x_new, dtype=float)
    for i in range(len(x) - 1):
        left = x[i]
        right = x[i + 1]
        mask = (x_new >= left) & (x_new <= right)
        if not np.any(mask):
            continue
        h = right - left
        t = (x_new[mask] - left) / h
        h00 = 2 * t**3 - 3 * t**2 + 1
        h10 = t**3 - 2 * t**2 + t
        h01 = -2 * t**3 + 3 * t**2
        h11 = t**3 - t**2
        y_new[mask] = h00 * y[i] + h10 * h * m[i] + h01 * y[i + 1] + h11 * h * m[i + 1]
    return y_new


def plot_coverage_over_time() -> None:
    apply_unsupported_pairs()
    df = pd.read_csv(DATA_DIR / "coverage_timeseries.csv")
    df["elapsed_hour"] = df["elapsed_min"] / 60.0
    df["coverage_pct"] = df["target_region_branch_coverage"] * 100.0
    max_pct = float(df["coverage_pct"].max()) if not df.empty else 70.0
    y_upper = min(100.0, max(70.0, math.ceil((max_pct + 5.0) / 10.0) * 10.0))
    fig, axes = plt.subplots(1, len(PLOT_DBMS_ORDER), figsize=(14.4, 3.6), constrained_layout=False)
    axes_list = list(axes.flat)
    for idx, dbms in enumerate(PLOT_DBMS_ORDER):
        ax = axes_list[idx]
        sub = df[df["dbms"] == dbms]
        for tool in PLOT_TOOLS:
            tdf = sub[sub["tool"] == tool]
            if tdf.empty:
                continue
            grouped = (
                tdf.groupby("elapsed_hour")["coverage_pct"]
                .agg(["mean", "std", "count"])
                .reset_index()
                .sort_values("elapsed_hour")
            )
            x = grouped["elapsed_hour"].to_numpy(float)
            y = grouped["mean"].to_numpy(float)
            draw_ci = bool((grouped["count"] >= 3).all())
            ci95 = (
                1.96
                * grouped["std"].fillna(0.0).to_numpy(float)
                / np.sqrt(grouped["count"].clip(lower=1).to_numpy(float))
            )
            if len(x):
                if x[0] <= 1e-9:
                    y[0] = 0.0
                    ci95[0] = 0.0
                else:
                    x = np.insert(x, 0, 0.0)
                    y = np.insert(y, 0, 0.0)
                    ci95 = np.insert(ci95, 0, 0.0)
            style = TOOL_STYLES[tool]
            if len(x) >= 2:
                x_smooth = np.linspace(x.min(), x.max(), 240)
                y_smooth = pchip_interpolate(x, y, x_smooth)
                if draw_ci:
                    lower = pchip_interpolate(x, np.maximum(y - ci95, 0), x_smooth)
                    upper = pchip_interpolate(x, np.minimum(y + ci95, 100), x_smooth)
                    ax.fill_between(x_smooth, lower, upper, color=style["color"], alpha=0.14, linewidth=0)
                ax.plot(x_smooth, y_smooth, color=style["color"], linewidth=2.1, label=tool)
            ax.plot(x, y, linestyle="None", marker=style["marker"], color=style["color"], markersize=5.5)
        ax.set_title(dbms, fontsize=12, fontweight="bold")
        ax.set_xlim(0, 24)
        ax.set_ylim(0, y_upper)
        ax.set_xticks([0, 5, 10, 15, 20, 24])
        ax.grid(True, linestyle="--", linewidth=0.5, color="#d8d8d8", alpha=0.45)
        if idx == 0:
            ax.set_ylabel("Target-region branch coverage (%)")
        ax.set_xlabel("Time (h)")
    handles, labels = axes_list[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=7, frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.20, top=0.78, wspace=0.28)
    HOUR24_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_flat_figures()
    fig.savefig(HOUR24_DIR / "rq2_coverage_over_time.png", dpi=240, bbox_inches="tight")
    fig.savefig(HOUR24_DIR / "rq2_coverage_over_time.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_heatmap() -> None:
    apply_unsupported_pairs()
    df = pd.read_csv(DATA_DIR / "component_heatmap.csv")
    df["dbms"] = df["dbms"].map(norm_dbms)
    df["component"] = df["component"].map(norm_component)
    agg = df.groupby(["dbms", "component", "tool"], as_index=False)[["risk_branches_hit", "risk_branches_total"]].sum()
    agg["coverage"] = np.where(
        agg["risk_branches_total"] > 0,
        agg["risk_branches_hit"] / agg["risk_branches_total"] * 100.0,
        0.0,
    )

    def matrix_for(dbms: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        sub = agg[agg["dbms"] == dbms]
        matrix = (
            sub.pivot(index="component", columns="tool", values="coverage")
            .reindex(index=COMPONENTS, columns=PLOT_TOOLS)
        )
        unsupported = pd.DataFrame(False, index=COMPONENTS, columns=PLOT_TOOLS)
        for tool in PLOT_TOOLS:
            if (tool, dbms) in UNSUPPORTED_TOOL_DBMS:
                unsupported.loc[:, tool] = True
            if (tool, dbms) in COMPONENT_HEATMAP_OMIT_TOOL_DBMS:
                unsupported.loc[:, tool] = True
        mask = unsupported
        matrix = matrix.fillna(0.0)
        labels = matrix.round(0).astype("Int64").astype(str)
        labels = labels.mask(mask, "")
        return matrix, labels, mask

    def draw_panel(ax: plt.Axes, dbms: str, *, cbar: bool, cbar_ax=None) -> None:
        matrix, labels, mask = matrix_for(dbms)
        ax.set_facecolor("#eeeeee")
        sns.heatmap(
            matrix,
            ax=ax,
            cmap="YlOrRd",
            vmin=0,
            vmax=75,
            annot=labels,
            fmt="",
            mask=mask,
            linewidths=0.6,
            linecolor="white",
            cbar=cbar,
            cbar_ax=cbar_ax,
            cbar_kws={"label": "Target-region branch coverage (%)"},
        )
        for row_idx, component in enumerate(COMPONENTS):
            for col_idx, tool in enumerate(PLOT_TOOLS):
                if mask.loc[component, tool]:
                    ax.text(col_idx + 0.5, row_idx + 0.5, "-", ha="center", va="center", color="#777777", fontsize=9)
        ax.set_title(dbms, fontsize=12.5, fontweight="bold")
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.tick_params(axis="x", rotation=45, labelsize=8.5)
        ax.tick_params(axis="y", rotation=0, labelsize=9)

    ensure_fig_dirs(dbms_names=DBMS_ORDER)
    fig, axes = plt.subplots(1, len(DBMS_ORDER), figsize=(16.0, 4.8), constrained_layout=False)
    cbar_ax = fig.add_axes([0.965, 0.22, 0.008, 0.58])
    for idx, dbms in enumerate(DBMS_ORDER):
        draw_panel(axes[idx], dbms, cbar=(idx == len(DBMS_ORDER) - 1), cbar_ax=cbar_ax if idx == len(DBMS_ORDER) - 1 else None)
        if idx > 0:
            axes[idx].set_yticklabels([])
        if idx < len(DBMS_ORDER) - 1:
            axes[idx].axvline(len(PLOT_TOOLS) + 0.25, color="#9a9a9a", linestyle="--", linewidth=0.8, clip_on=False)
    fig.suptitle("RQ2 Component-Level Target-Region Coverage by DBMS and Tool", fontsize=14, fontweight="bold", y=1.02)
    fig.subplots_adjust(left=0.055, right=0.955, bottom=0.20, top=0.84, wspace=0.18)
    for stem in ("rq2_component_heatmap", "rq2_component_heatmap_by_dbms"):
        fig.savefig(HEATMAP_DIR / f"{stem}.png", dpi=240, bbox_inches="tight")
        fig.savefig(HEATMAP_DIR / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)

    for dbms in DBMS_ORDER:
        fig, ax = plt.subplots(figsize=(5.4, 4.8))
        draw_panel(ax, dbms, cbar=True)
        fig.tight_layout()
        slug = dbms_slug(dbms)
        out_dir = HEATMAP_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_dir / f"rq2_component_heatmap_{slug}.png", dpi=240, bbox_inches="tight")
        fig.savefig(out_dir / f"rq2_component_heatmap_{slug}.pdf", bbox_inches="tight")
        plt.close(fig)


def write_readme(real_data: list[Path] | None) -> None:
    readme_path = RESULT_DIR / "README.md"
    sources: list[str] = []
    if readme_path.exists():
        for line in readme_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.fullmatch(r"- `(/root/SQLeek/.+)`", line)
            if match and match.group(1) not in sources:
                sources.append(match.group(1))
    for path in real_data or []:
        value = str(path)
        if value not in sources:
            sources.append(value)
    source_lines = "\n".join(f"- `{path}`" for path in sources)
    if not source_lines:
        source_lines = "- See `data/runs.csv` and the per-tool audit directories."
    text = f"""# RQ2 Result Package

This directory contains the real RQ2 replay results used to generate the
result tables and figures.

Recorded replay sources:

{source_lines}
- SQLancer/MonetDB is represented by a local branch-only aggregate; its
  unavailable target-region/global metrics are omitted from the table and
  component heatmap. SQLaser/MariaDB now has five validated LLVM replay repeats in audit/sqlaser_mariadb.

Coverage denominators are locked in `data/coverage_denominator_lock.csv`.
The merge/refresh script rejects any formal row whose target-branch,
target-region, or global-branch denominator differs from that lock. All
tool--DBMS rows are retained. The denominator policy is recorded in
`audit/denominator_repair_manifest.json`.

Files under `data/` are the canonical structured inputs for the plotting and
table flow. Files under `audit/<tool>_<dbms>/` preserve target-region and
target-branch audit CSVs from the real replays.
"""
    readme_path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not RESULT_DIR.exists():
        raise SystemExit(f"missing result directory: {RESULT_DIR}")
    if args.refresh_only and args.real_data:
        raise SystemExit("--refresh-only cannot be combined with --real-data")
    if not args.refresh_only and not args.real_data:
        raise SystemExit("--real-data is required unless --refresh-only is used")

    apply_unsupported_pairs()
    for real_data in args.real_data or []:
        merge_real_rows(real_data, args.tool)
    validate_denominators(
        read_csv(DATA_DIR / "coverage_summary.csv"),
        "formal result/data/coverage_summary.csv",
    )
    write_three_metric_tables()
    write_timeseries_stats()
    plot_coverage_over_time()
    plot_heatmap()
    write_readme(args.real_data)
    action = "refreshed" if args.refresh_only else "merged real rows into"
    print(f"{action} {RESULT_DIR}")
    for path in iter_generated_figures():
        print(path)


if __name__ == "__main__":
    main()
