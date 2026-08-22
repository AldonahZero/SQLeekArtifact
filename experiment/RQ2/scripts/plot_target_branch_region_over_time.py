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
PLOT_TOOLS = ["SQLeek", "SQLaser", "AFLGo", "Griffin", "SQUIRREL", "DynSQL", "SQLancer"]
PLOT_DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
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


def metric_frame(metric: str) -> pd.DataFrame:
    ts = pd.read_csv(DATA / "coverage_timeseries.csv")
    summary = pd.read_csv(DATA / "coverage_summary.csv")
    totals = summary.groupby(["tool", "dbms", "repeat_id"], as_index=False).agg(
        risk_targets_total=("risk_targets_total", "max")
    )
    df = ts.merge(totals, on=["tool", "dbms", "repeat_id"], how="left")
    df["elapsed_hour"] = df["elapsed_min"] / 60.0
    if metric == "target_branch":
        df["metric_pct"] = df["target_region_branch_coverage"] * 100.0
    elif metric == "target_region":
        df["metric_pct"] = np.where(
            df["risk_targets_total"] > 0,
            df["risk_targets_hit"] / df["risk_targets_total"] * 100.0,
            0.0,
        )
    else:
        raise ValueError(metric)
    return df


def plot_metric(metric: str, ylabel: str, stem: str, ylim: tuple[float, float]) -> None:
    df = metric_frame(metric)
    if metric == "target_branch" and not df.empty:
        max_pct = float(df["metric_pct"].max())
        ylim = (0, min(100.0, max(70.0, math.ceil((max_pct + 5.0) / 10.0) * 10.0)))
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
                tdf.groupby("elapsed_hour")["metric_pct"]
                .agg(["mean", "std", "count"])
                .reset_index()
                .sort_values("elapsed_hour")
            )
            x = grouped["elapsed_hour"].to_numpy(float)
            y = grouped["mean"].to_numpy(float)
            draw_ci = bool((grouped["count"] >= 3).all())
            ci95 = 1.96 * grouped["std"].fillna(0.0).to_numpy(float) / np.sqrt(grouped["count"].clip(lower=1).to_numpy(float))
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
                    lower = pchip_interpolate(x, np.maximum(y - ci95, ylim[0]), x_smooth)
                    upper = pchip_interpolate(x, np.minimum(y + ci95, ylim[1]), x_smooth)
                    ax.fill_between(x_smooth, lower, upper, color=style["color"], alpha=0.14, linewidth=0)
                ax.plot(x_smooth, y_smooth, color=style["color"], linewidth=2.1, label=tool)
            ax.plot(x, y, linestyle="None", marker=style["marker"], color=style["color"], markersize=5.5)
        ax.set_title(dbms, fontsize=12, fontweight="bold")
        ax.set_xlim(0, 24)
        ax.set_ylim(*ylim)
        ax.set_xticks([0, 5, 10, 15, 20, 24])
        ax.grid(True, linestyle="--", linewidth=0.5, color="#d8d8d8", alpha=0.45)
        if idx == 0:
            ax.set_ylabel(ylabel)
        ax.set_xlabel("Time (h)")
    handles, labels = axes_list[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=7, frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.20, top=0.78, wspace=0.28)
    HOUR24_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_flat_figures()
    fig.savefig(HOUR24_DIR / f"{stem}.png", dpi=240, bbox_inches="tight")
    fig.savefig(HOUR24_DIR / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    plot_metric("target_branch", "Target-region branch coverage (%)", "rq2_target_branch_over_time", (0, 70))
    plot_metric("target_region", "Target-region hit coverage (%)", "rq2_target_region_over_time", (0, 100))


if __name__ == "__main__":
    main()
