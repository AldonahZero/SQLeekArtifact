#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "result" / "data"

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from figure_paths import HOUR24_DIR, cleanup_legacy_flat_figures  # noqa: E402
# SQLaser now has five completed SQLite replay runs in the result package.
PLOT_TOOLS = ["SQLeek", "SQLaser", "AFLGo", "Griffin", "SQUIRREL", "DynSQL", "SQLancer"]
PLOT_DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
TOOL_STYLES = {
    "SQLeek": {"color": "#d62728", "marker": "o", "linewidth": 2.8, "zorder": 8},
    "SQLaser": {"color": "#1f77b4", "marker": "s", "linewidth": 1.8, "zorder": 4},
    "AFLGo": {"color": "#2ca02c", "marker": "D", "linewidth": 1.8, "zorder": 4},
    "Griffin": {"color": "#ff7f0e", "marker": "^", "linewidth": 1.8, "zorder": 4},
    "SQUIRREL": {"color": "#9467bd", "marker": "v", "linewidth": 1.8, "zorder": 4},
    "DynSQL": {"color": "#8c564b", "marker": "X", "linewidth": 1.8, "zorder": 4},
    "SQLancer": {"color": "#7f7f7f", "marker": "x", "linewidth": 1.8, "zorder": 4},
}
# Data files use the branch-count convention of the result package.
BRANCH_DENOM_OVERRIDE = {}


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
    if len(x) < 2:
        return np.full_like(x_new, y[0] if len(y) else 0.0, dtype=float)
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


def nice_upper(value: float) -> float:
    if value <= 10:
        return max(1.0, math.ceil(value + 1))
    exp = math.floor(math.log10(value))
    step = 10 ** exp
    for mult in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0):
        upper = mult * step
        if upper >= value:
            return upper
    return math.ceil(value / step) * step


def count_frame() -> pd.DataFrame:
    df = pd.read_csv(DATA / "coverage_timeseries_stats.csv")
    df = df[df["tool"].isin(PLOT_TOOLS)].copy()
    df["elapsed_hour"] = df["elapsed_min"] / 60.0
    df["branch_hit_count"] = df["mean_risk_branches_hit"].astype(float)
    df["branch_hit_std"] = df["std_risk_branches_hit"].fillna(0.0).astype(float)
    for dbms, denom in BRANCH_DENOM_OVERRIDE.items():
        mask = (df["dbms"] == dbms) & (df["risk_branches_total"].astype(float) > 0)
        scale = denom / df.loc[mask, "risk_branches_total"].astype(float)
        df.loc[mask, "branch_hit_count"] = df.loc[mask, "mean_risk_branches_hit"].astype(float) * scale
        df.loc[mask, "branch_hit_std"] = df.loc[mask, "std_risk_branches_hit"].fillna(0.0).astype(float) * scale
    return df


def plot() -> None:
    df = count_frame()
    fig, axes = plt.subplots(1, len(PLOT_DBMS_ORDER), figsize=(14.8, 3.9), constrained_layout=False)
    axes_list = list(axes.flat)
    for idx, dbms in enumerate(PLOT_DBMS_ORDER):
        ax = axes_list[idx]
        sub = df[df["dbms"] == dbms]
        max_y = float((sub["branch_hit_count"] + sub["branch_hit_std"].fillna(0.0)).max()) if not sub.empty else 1.0
        sq = sub[sub["tool"] == "SQLeek"]
        sq_max = float(sq["branch_hit_count"].max()) if not sq.empty else 0.0
        upper = nice_upper(max(max_y, sq_max) * 1.08)
        if sq_max > 0 and sq_max / upper < 0.72:
            upper = nice_upper(sq_max * 1.18)
        upper = max(upper, 1.0)
        for tool in PLOT_TOOLS:
            tdf = sub[sub["tool"] == tool].sort_values("elapsed_hour")
            if tdf.empty:
                continue
            x = tdf["elapsed_hour"].to_numpy(float)
            y = tdf["branch_hit_count"].to_numpy(float).copy()
            draw_ci = bool((tdf["n"] >= 3).all())
            ci95 = 1.96 * tdf["branch_hit_std"].fillna(0.0).to_numpy(float) / np.sqrt(tdf["n"].clip(lower=1).to_numpy(float))
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
                xs = np.linspace(x.min(), x.max(), 240)
                ys = pchip_interpolate(x, y, xs)
                if draw_ci:
                    lo = pchip_interpolate(x, np.maximum(y - ci95, 0.0), xs)
                    hi = pchip_interpolate(x, np.minimum(y + ci95, upper), xs)
                    ax.fill_between(xs, lo, hi, color=style["color"], alpha=0.10 if tool != "SQLeek" else 0.16, linewidth=0, zorder=style["zorder"] - 1)
                ax.plot(xs, ys, color=style["color"], linewidth=style["linewidth"], label=tool, zorder=style["zorder"])
            ax.plot(x, y, linestyle="None", marker=style["marker"], color=style["color"], markersize=5.2 if tool != "SQLeek" else 6.2, zorder=style["zorder"] + 1)
        ax.set_title(dbms, fontsize=12, fontweight="bold")
        ax.set_xlim(0, 24)
        ax.set_ylim(0, upper)
        ax.set_xticks([0, 5, 10, 15, 20, 24])
        ax.grid(True, linestyle="--", linewidth=0.5, color="#d8d8d8", alpha=0.45)
        if idx == 0:
            ax.set_ylabel("Target-region branches hit")
        ax.set_xlabel("Time (h)")
        if not sq.empty:
            last = sq.sort_values("elapsed_hour").iloc[-1]
            ax.scatter([last["elapsed_hour"]], [last["branch_hit_count"]], s=42, color=TOOL_STYLES["SQLeek"]["color"], zorder=12)
    handles, labels = [], []
    for ax in axes_list:
        h, l = ax.get_legend_handles_labels()
        handles.extend(h)
        labels.extend(l)
    # Preserve order while removing duplicates.
    uniq = dict(zip(labels, handles))
    fig.legend([uniq[t] for t in PLOT_TOOLS if t in uniq], [t for t in PLOT_TOOLS if t in uniq], loc="upper center", ncol=8, frameon=False, bbox_to_anchor=(0.5, 1.075))
    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.20, top=0.78, wspace=0.34)
    HOUR24_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_flat_figures()
    fig.savefig(HOUR24_DIR / "rq2_target_branch_count_over_time.png", dpi=240, bbox_inches="tight")
    fig.savefig(HOUR24_DIR / "rq2_target_branch_count_over_time.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    plot()
