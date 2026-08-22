#!/usr/bin/env python3
"""Plot RQ4 target-region branch counts for the full and ablated variants."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator


ROOT = Path(__file__).resolve().parents[2]
RQ4 = ROOT / "experiment/RQ4"
RQ4_STATS = RQ4 / "result/data/coverage_timeseries_stats.csv"
RQ3_STATS = ROOT / "experiment/RQ3/result/data/rq3_coverage_timeseries_stats.csv"
OUT = RQ4 / "result/figures/24h"

DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
CHECKPOINTS = [60, 180, 300, 480, 600, 720, 900, 1200, 1440]
# Match the compact paper-style plots.  Markers are intentionally omitted so
# nearby curves remain legible between the nine time checkpoints.
VARIANTS = ("Full", "W/O-M1", "W/O-M2", "W/O-M3")
DISPLAY_LABELS = {
    "Full": "SQLeek",
    "W/O-M1": r"SQLeek$^{risk-}$",
    "W/O-M2": r"SQLeek$^{context-}$",
    "W/O-M3": r"SQLeek$^{directed-}$",
}
COLORS = {
    "Full": "#E69F00",    # orange solid
    "W/O-M1": "#7E57C2",  # purple dashed
    "W/O-M2": "#0072B2",  # blue dash-dot
    "W/O-M3": "#009E73",  # green dotted
}
LINESTYLES = {
    "Full": "-",
    "W/O-M1": (0, (5.5, 2.2)),
    "W/O-M2": (0, (5.2, 2.2, 1.2, 2.2)),
    "W/O-M3": (0, (1.0, 1.8)),
}
LINEWIDTHS = {"Full": 1.05, "W/O-M1": 0.95, "W/O-M2": 0.95, "W/O-M3": 0.95}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def absolute_branch_values(
    row: dict[str, str], risk_branches_total: float
) -> tuple[float, float]:
    """Return mean hit count and the existing 95% interval in branch units."""
    mean_hits = float(row["mean_risk_branches_hit"])
    ci95_coverage = float(row.get("ci95_target_region_branch_coverage") or 0.0)
    return mean_hits, ci95_coverage * risk_branches_total


def load_risk_branch_totals() -> dict[str, float]:
    """Get one denominator per DBMS for converting Full's CI to branch units."""
    totals: dict[str, float] = {}
    for row in read_rows(RQ4_STATS):
        dbms = row["dbms"]
        if dbms not in DBMS_ORDER:
            continue
        try:
            total = float(row["risk_branches_total"])
        except (KeyError, TypeError, ValueError):
            continue
        if total > 0:
            totals[dbms] = max(totals.get(dbms, 0.0), total)
    return totals


def load_series() -> dict[tuple[str, str], dict[int, tuple[float, float]]]:
    series: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}
    risk_branch_totals = load_risk_branch_totals()

    for row in read_rows(RQ4_STATS):
        dbms = row["dbms"]
        tool_map = {
            "SQLeek-W/O-M1": "W/O-M1",
            "SQLeek-W/O-M2": "W/O-M2",
            "SQLeek-W/O-M3": "W/O-M3",
            "SQLeek-W/O-Risk-AwareDirected": "W/O-M3",
        }
        if dbms not in DBMS_ORDER or row["tool"] not in tool_map:
            continue
        minute = int(float(row["elapsed_min"]))
        if minute > 0:
            mean_hits, ci95_hits = absolute_branch_values(
                row, float(row["risk_branches_total"])
            )
            series.setdefault((dbms, tool_map[row["tool"]]), {})[minute] = (
                mean_hits,
                ci95_hits,
            )

    for row in read_rows(RQ3_STATS):
        if row["tool"] == "SQLeek" and row["dbms"] in DBMS_ORDER:
            minute = int(float(row["elapsed_min"]))
            if minute > 0:
                mean_hits, ci95_hits = absolute_branch_values(
                    row, risk_branch_totals[row["dbms"]]
                )
                series.setdefault((row["dbms"], "Full"), {})[minute] = (
                    mean_hits,
                    ci95_hits,
                )

    for key in [
        (dbms, tool)
        for dbms in DBMS_ORDER
        for tool in VARIANTS
    ]:
        series.setdefault(key, {})
        if series[key]:
            series[key][0] = (0.0, 0.0)
    return series


def nice_upper(value: float) -> float:
    """Return a rounded upper bound with enough headroom for the CI band."""
    if value <= 10:
        return max(1.0, math.ceil(value + 1))
    exponent = math.floor(math.log10(value))
    step = 10**exponent
    for multiplier in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0):
        upper = multiplier * step
        if upper >= value:
            return upper
    return math.ceil(value / step) * step


def main() -> None:
    series = load_series()
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9.5,
            "axes.titleweight": "bold",
            "legend.fontsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, axes_grid = plt.subplots(2, 2, figsize=(5.0, 3.65), sharex=True)
    axes = axes_grid.ravel()
    for axis, dbms in zip(axes, DBMS_ORDER):
        for tool in VARIANTS:
            values = series[(dbms, tool)]
            if not any(m in values for m in CHECKPOINTS):
                continue
            minutes = [0] + [m for m in CHECKPOINTS if m in values]
            branch_hits = [values[m][0] for m in minutes]
            ci95 = [values[m][1] for m in minutes]
            hours = [m / 60 for m in minutes]
            axis.plot(
                hours,
                branch_hits,
                label=DISPLAY_LABELS[tool],
                color=COLORS[tool],
                linestyle=LINESTYLES[tool],
                linewidth=LINEWIDTHS[tool],
            )
            axis.fill_between(
                hours,
                [max(0.0, mean - ci) for mean, ci in zip(branch_hits, ci95)],
                [mean + ci for mean, ci in zip(branch_hits, ci95)],
                color=COLORS[tool],
                alpha=0.06,
                linewidth=0,
            )
        axis.set_title(dbms)
        axis.set_xlim(0, 24)
        max_value = max(
            (
                mean + ci
                for tool in VARIANTS
                for mean, ci in series[(dbms, tool)].values()
            ),
            default=1.0,
        )
        axis.set_ylim(0, nice_upper(max_value * 1.08))
        axis.set_xticks([0, 5, 10, 15, 20, 24])
        axis.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        axis.grid(axis="both", color="#d9d9d9", linestyle="--", linewidth=0.7)
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)

    fig.text(0.5, 0.045, "Time (h)", ha="center", va="center", fontsize=8)
    fig.text(
        0.055,
        0.5,
        "Covered Risk-Region Branches",
        ha="center",
        va="center",
        rotation="vertical",
        fontsize=8,
    )
    handles = [
        Line2D(
            [0],
            [0],
            color=COLORS[variant],
            linestyle=LINESTYLES[variant],
            linewidth=LINEWIDTHS[variant],
            label=DISPLAY_LABELS[variant],
        )
        for variant in VARIANTS
    ]
    labels = [DISPLAY_LABELS[variant] for variant in VARIANTS]
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.005),
        ncol=4,
        frameon=False,
    )
    fig.subplots_adjust(
        left=0.16,
        right=0.99,
        bottom=0.12,
        top=0.82,
        wspace=0.45,
        hspace=0.45,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        OUT / "rq4_target_branch_coverage_over_time.png",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0,
    )
    fig.savefig(
        OUT / "rq4_target_branch_coverage_over_time.pdf",
        bbox_inches="tight",
        pad_inches=0,
    )
    plt.close(fig)


if __name__ == "__main__":
    main()
