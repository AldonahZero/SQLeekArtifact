#!/usr/bin/env python3
"""Create the 2x2 RQ3 target-region branch-coverage figure.

This is the 2x2 paper-media variant of
``experiment/RQ2/scripts/plot_target_branch_region_over_time.py``.  It keeps
the same data, smoothing, tool styles, and confidence-band policy, while
using the RQ3 paper filename and the correct percentage y-axis label.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[2]
DATA = PROJECT / "experiment/RQ2/result/data"
OUTPUT = PROJECT.parent / "SQLeek-Paper/media/RQ3_Target_Branch_Coverage_Over_Time-crop.pdf"

PLOT_TOOLS = ["SQLeek", "SQLaser", "AFLGo", "Griffin", "SQUIRREL", "DynSQL", "SQLancer"]
PLOT_DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
Y_LABEL = "Target-region branch coverage (%)"
TOOL_STYLES = {
    "SQLeek": {"color": "#d62728", "marker": "o"},
    "SQLaser": {"color": "#1f77b4", "marker": "s"},
    "AFLGo": {"color": "#2ca02c", "marker": "D"},
    "Griffin": {"color": "#ff7f0e", "marker": "^"},
    "SQUIRREL": {"color": "#9467bd", "marker": "v"},
    "DynSQL": {"color": "#8c564b", "marker": "X"},
    "SQLancer": {"color": "#7f7f7f", "marker": "x"},
}


def pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = len(x)
    h = np.diff(x)
    delta = np.diff(y) / h
    slopes = np.zeros(n, dtype=float)
    if n == 2:
        slopes[:] = delta[0]
        return slopes
    slopes[0] = ((2 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
    if slopes[0] * delta[0] <= 0:
        slopes[0] = 0.0
    elif abs(slopes[0]) > 3 * abs(delta[0]):
        slopes[0] = 3 * delta[0]
    slopes[-1] = ((2 * h[-1] + h[-2]) * delta[-1] - h[-1] * delta[-2]) / (h[-1] + h[-2])
    if slopes[-1] * delta[-1] <= 0:
        slopes[-1] = 0.0
    elif abs(slopes[-1]) > 3 * abs(delta[-1]):
        slopes[-1] = 3 * delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            slopes[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            slopes[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return slopes


def pchip_interpolate(x: np.ndarray, y: np.ndarray, x_new: np.ndarray) -> np.ndarray:
    slopes = pchip_slopes(x, y)
    y_new = np.empty_like(x_new, dtype=float)
    for i in range(len(x) - 1):
        left, right = x[i], x[i + 1]
        mask = (x_new >= left) & (x_new <= right)
        if not np.any(mask):
            continue
        h = right - left
        t = (x_new[mask] - left) / h
        h00 = 2 * t**3 - 3 * t**2 + 1
        h10 = t**3 - 2 * t**2 + t
        h01 = -2 * t**3 + 3 * t**2
        h11 = t**3 - t**2
        y_new[mask] = (
            h00 * y[i]
            + h10 * h * slopes[i]
            + h01 * y[i + 1]
            + h11 * h * slopes[i + 1]
        )
    return y_new


def metric_frame() -> pd.DataFrame:
    timeseries = pd.read_csv(DATA / "coverage_timeseries.csv")
    stats = pd.read_csv(DATA / "coverage_timeseries_stats.csv")
    repeat_counts = timeseries.groupby(
        ["tool", "dbms", "elapsed_min"], as_index=False
    ).size().rename(columns={"size": "repeat_count"})
    frame = stats.merge(
        repeat_counts,
        on=["tool", "dbms", "elapsed_min"],
        how="left",
    )
    frame["elapsed_hour"] = frame["elapsed_min"] / 60.0
    frame["metric_pct"] = frame["mean_target_region_branch_coverage"] * 100.0
    frame["std_pct"] = frame["std_target_region_branch_coverage"] * 100.0
    return frame


def main() -> None:
    frame = metric_frame()
    plot_frame = frame[frame["dbms"].isin(PLOT_DBMS_ORDER)]
    max_pct = float(plot_frame["metric_pct"].max())
    ylim = (0.0, min(100.0, max(70.0, math.ceil((max_pct + 5.0) / 10.0) * 10.0)))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "legend.fontsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, axes_grid = plt.subplots(
        2,
        2,
        figsize=(6.8, 5.1),
        sharex=True,
        sharey=True,
    )
    axes = axes_grid.ravel()
    for index, (axis, dbms) in enumerate(zip(axes, PLOT_DBMS_ORDER)):
        subset = plot_frame[plot_frame["dbms"] == dbms]
        for tool in PLOT_TOOLS:
            tool_rows = subset[subset["tool"] == tool]
            if tool_rows.empty:
                continue
            grouped = tool_rows.sort_values("elapsed_hour").copy()
            x = grouped["elapsed_hour"].to_numpy(float)
            y = grouped["metric_pct"].to_numpy(float).copy()
            repeat_count = grouped["repeat_count"].fillna(1).to_numpy(float)
            draw_ci = bool((repeat_count >= 3).all())
            ci95 = (
                1.96
                * grouped["std_pct"].fillna(0.0).to_numpy(float)
                / np.sqrt(np.maximum(repeat_count, 1.0))
            )
            ci95 = ci95.copy()
            if len(x) and x[0] <= 1e-9:
                y[0] = 0.0
                ci95[0] = 0.0
            elif len(x):
                x = np.insert(x, 0, 0.0)
                y = np.insert(y, 0, 0.0)
                ci95 = np.insert(ci95, 0, 0.0)

            style = TOOL_STYLES[tool]
            if len(x) >= 2:
                x_smooth = np.linspace(x.min(), x.max(), 240)
                y_smooth = pchip_interpolate(x, y, x_smooth)
                if draw_ci:
                    lower = pchip_interpolate(
                        x, np.maximum(y - ci95, ylim[0]), x_smooth
                    )
                    upper = pchip_interpolate(
                        x, np.minimum(y + ci95, ylim[1]), x_smooth
                    )
                    axis.fill_between(
                        x_smooth,
                        lower,
                        upper,
                        color=style["color"],
                        alpha=0.14,
                        linewidth=0,
                    )
                axis.plot(
                    x_smooth,
                    y_smooth,
                    color=style["color"],
                    linewidth=1.8,
                )
            axis.plot(
                x,
                y,
                linestyle="None",
                marker=style["marker"],
                color=style["color"],
                markersize=4.5,
            )

        axis.set_title(dbms)
        axis.set_xlim(0, 24)
        axis.set_ylim(*ylim)
        axis.set_xticks([0, 5, 10, 15, 20, 24])
        axis.set_yticks([0, 20, 40, 60, 80])
        axis.grid(True, linestyle="--", linewidth=0.6, color="#d8d8d8", alpha=0.7)
        if index // 2 == 1:
            axis.set_xlabel("Time (h)")

    handles = [
        Line2D(
            [0],
            [0],
            color=TOOL_STYLES[tool]["color"],
            linewidth=1.8,
            label=tool,
        )
        for tool in PLOT_TOOLS
    ]
    fig.legend(
        handles,
        PLOT_TOOLS,
        loc="upper center",
        ncol=7,
        frameon=False,
        bbox_to_anchor=(0.5, 0.995),
    )
    fig.supxlabel("Time (h)", y=0.035, fontsize=8)
    fig.supylabel(Y_LABEL, x=0.025, fontsize=8)
    fig.subplots_adjust(
        left=0.10,
        right=0.99,
        bottom=0.12,
        top=0.83,
        wspace=0.28,
        hspace=0.38,
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


if __name__ == "__main__":
    main()
