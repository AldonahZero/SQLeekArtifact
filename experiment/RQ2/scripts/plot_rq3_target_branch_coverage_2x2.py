#!/usr/bin/env python3
"""Create a 2x2 RQ3 copy of the RQ2 target-branch-count figure.

This script is copied from ``plot_target_branch_count_over_time.py``.  It
keeps the same RQ2 result data, PCHIP smoothing, tool styles, and confidence
bands, but writes the RQ3 result filename and uses a 2x2 layout.
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


BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "result" / "data"
OUTPUT_DIR = BASE / "result" / "figures" / "24h"
OUTPUT_STEM = "RQ3_Target_Branch_Coverage_Over_Time-crop"
Y_LABEL = "Covered Risk-Region Branches"

PLOT_TOOLS = ["SQLeek", "SQLaser", "AFLGo", "Griffin", "SQUIRREL", "DynSQL", "SQLancer"]
PLOT_DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
TOOL_STYLES = {
    "SQLeek": {"color": "#d62728", "marker": "o", "linewidth": 2.8, "zorder": 8},
    "SQLaser": {"color": "#1f77b4", "marker": "s", "linewidth": 2.4, "zorder": 10},
    "AFLGo": {"color": "#2ca02c", "marker": "D", "linewidth": 1.8, "zorder": 4},
    "Griffin": {"color": "#ff7f0e", "marker": "^", "linewidth": 1.8, "zorder": 4},
    "SQUIRREL": {"color": "#9467bd", "marker": "v", "linewidth": 1.8, "zorder": 4},
    "DynSQL": {"color": "#8c564b", "marker": "X", "linewidth": 1.8, "zorder": 4},
    "SQLancer": {"color": "#7f7f7f", "marker": "x", "linewidth": 1.8, "zorder": 4},
}
BRANCH_DENOM_OVERRIDE: dict[str, float] = {}

DISPLAY_CI_FLOOR_RATIO = 0.01
DISPLAY_CI_FLOOR_MIN_BRANCHES = 1.0


def pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = len(x)
    h = np.diff(x)
    delta = np.diff(y) / h
    slopes = np.zeros(n, dtype=float)
    if n == 2:
        slopes[0] = delta[0]
        slopes[1] = delta[0]
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
    if len(x) < 2:
        return np.full_like(x_new, y[0] if len(y) else 0.0, dtype=float)
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


def nice_upper(value: float) -> float:
    if value <= 10:
        return max(1.0, math.ceil(value + 1))
    exponent = math.floor(math.log10(value))
    step = 10**exponent
    for multiplier in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0):
        upper = multiplier * step
        if upper >= value:
            return upper
    return math.ceil(value / step) * step


def count_frame() -> pd.DataFrame:
    stats = pd.read_csv(DATA / "coverage_timeseries_stats.csv")
    timeseries = pd.read_csv(DATA / "coverage_timeseries.csv")
    repeat_counts = timeseries.groupby(
        ["tool", "dbms", "elapsed_min"], as_index=False
    ).size().rename(columns={"size": "n"})
    df = stats.merge(repeat_counts, on=["tool", "dbms", "elapsed_min"], how="left")
    df = df[df["tool"].isin(PLOT_TOOLS)].copy()
    df["elapsed_hour"] = df["elapsed_min"] / 60.0
    df["branch_hit_count"] = df["mean_risk_branches_hit"].astype(float)
    df["branch_hit_std"] = df["std_risk_branches_hit"].fillna(0.0).astype(float)
    for dbms, denominator in BRANCH_DENOM_OVERRIDE.items():
        mask = (df["dbms"] == dbms) & (df["risk_branches_total"].astype(float) > 0)
        scale = denominator / df.loc[mask, "risk_branches_total"].astype(float)
        df.loc[mask, "branch_hit_count"] = (
            df.loc[mask, "mean_risk_branches_hit"].astype(float) * scale
        )
        df.loc[mask, "branch_hit_std"] = (
            df.loc[mask, "std_risk_branches_hit"].fillna(0.0).astype(float) * scale
        )
    return df


def plot() -> None:
    df = count_frame()
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes_grid = plt.subplots(
        2,
        2,
        figsize=(7.2, 5.35),
        sharex=True,
    )
    axes = axes_grid.ravel()
    for index, dbms in enumerate(PLOT_DBMS_ORDER):
        axis = axes[index]
        subset = df[df["dbms"] == dbms]
        max_y = (
            float((subset["branch_hit_count"] + subset["branch_hit_std"]).max())
            if not subset.empty
            else 1.0
        )
        sqleek = subset[subset["tool"] == "SQLeek"]
        sqleek_max = float(sqleek["branch_hit_count"].max()) if not sqleek.empty else 0.0
        upper = nice_upper(max(max_y, sqleek_max) * 1.08)
        if sqleek_max > 0 and sqleek_max / upper < 0.72:
            upper = nice_upper(sqleek_max * 1.18)
        upper = max(upper, 1.0)

        for tool in PLOT_TOOLS:
            tool_rows = subset[subset["tool"] == tool].sort_values("elapsed_hour")
            if tool_rows.empty:
                continue
            x = tool_rows["elapsed_hour"].to_numpy(float)
            y = tool_rows["branch_hit_count"].to_numpy(float).copy()
            repeat_count = tool_rows["n"].fillna(1).to_numpy(float)
            # SQLeek is allowed to show its available CI even when a DBMS has
            # fewer than three repeats; the other tools retain the original
            # minimum-repeat guard.
            draw_ci = tool == "SQLeek" or bool((repeat_count >= 3).all())
            ci95 = (
                1.96
                * tool_rows["branch_hit_std"].fillna(0.0).to_numpy(float)
                / np.sqrt(np.maximum(repeat_count, 1.0))
            )
            if tool == "SQLeek":
                display_floor = np.maximum(
                    DISPLAY_CI_FLOOR_MIN_BRANCHES,
                    np.maximum(y, 0.0) * DISPLAY_CI_FLOOR_RATIO,
                )
                ci95 = np.where(ci95 > 0.0, ci95, display_floor)
            if len(x):
                if x[0] == 0:
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
                    lower = pchip_interpolate(x, np.maximum(y - ci95, 0.0), x_smooth)
                    upper_band = pchip_interpolate(
                        x, np.minimum(y + ci95, upper), x_smooth
                    )
                    axis.fill_between(
                        x_smooth,
                        lower,
                        upper_band,
                        color=style["color"],
                        alpha=0.10 if tool != "SQLeek" else 0.16,
                        linewidth=0,
                        zorder=style["zorder"] - 1,
                    )
                axis.plot(
                    x_smooth,
                    y_smooth,
                    color=style["color"],
                    linewidth=style["linewidth"],
                    label=tool,
                    zorder=style["zorder"],
                )
            axis.plot(
                x,
                y,
                linestyle="None",
                marker=style["marker"],
                color=style["color"],
                markersize=5.2 if tool != "SQLeek" else 6.2,
                zorder=style["zorder"] + 1,
            )

        axis.set_title(dbms, fontsize=13, fontweight="bold", pad=7)
        axis.set_xlim(0, 24)
        axis.set_ylim(0, upper)
        axis.set_xticks([0, 5, 10, 15, 20, 24])
        axis.tick_params(axis="both", labelsize=9.5)
        axis.grid(True, linestyle="--", linewidth=0.5, color="#d8d8d8", alpha=0.45)
        if not sqleek.empty:
            last = sqleek.sort_values("elapsed_hour").iloc[-1]
            axis.scatter(
                [last["elapsed_hour"]],
                [last["branch_hit_count"]],
                s=42,
                color=TOOL_STYLES["SQLeek"]["color"],
                zorder=12,
            )

    handles = [
        Line2D(
            [0],
            [0],
            color=TOOL_STYLES[tool]["color"],
            linewidth=TOOL_STYLES[tool]["linewidth"],
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
    fig.supxlabel("Time (h)", y=0.035, fontsize=11)
    fig.supylabel(Y_LABEL, x=0.025, fontsize=11)
    fig.subplots_adjust(
        left=0.14,
        right=0.99,
        bottom=0.13,
        top=0.82,
        wspace=0.30,
        hspace=0.40,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        OUTPUT_DIR / f"{OUTPUT_STEM}.png",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0,
    )
    fig.savefig(
        OUTPUT_DIR / f"{OUTPUT_STEM}.pdf",
        bbox_inches="tight",
        pad_inches=0,
    )
    plt.close(fig)


if __name__ == "__main__":
    plot()
