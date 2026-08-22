#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "result" / "data"

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from figure_paths import HOUR24_DIR, cleanup_legacy_flat_figures  # noqa: E402


def main() -> None:
    stats = pd.read_csv(DATA / "coverage_timeseries_stats.csv")
    df = stats[(stats["tool"] == "SQLeek") & (stats["dbms"] == "MySQL")].copy()
    if df.empty:
        raise SystemExit("missing SQLeek/MySQL timeseries stats")
    df = df.sort_values("elapsed_min")
    df["hour"] = df["elapsed_min"] / 60.0
    df["target_branch_pct"] = df["mean_target_region_branch_coverage"] * 100.0
    df["target_region_pct"] = df["mean_target_region_hit_coverage"] * 100.0
    df["global_branch_pct"] = df["mean_global_branch_coverage"] * 100.0
    first = df[df["elapsed_min"] == 60].iloc[0]
    df["target_branch_delta"] = df["target_branch_pct"] - float(first["target_branch_pct"])
    df["global_branch_delta"] = df["global_branch_pct"] - float(first["global_branch_pct"])

    HOUR24_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_flat_figures()
    fig, axes = plt.subplots(1, 2, figsize=(9.8, 3.4), constrained_layout=False)

    ax = axes[0]
    ax.plot(df["hour"], df["target_branch_pct"], color="#d62728", marker="o", linewidth=2.2, label="Target branches")
    ax.plot(df["hour"], df["target_region_pct"], color="#b2182b", marker="s", linewidth=1.8, linestyle="--", label="Target regions")
    ax.set_xlim(1, 24)
    ymin = min(df["target_branch_pct"].min(), df["target_region_pct"].min()) - 0.25
    ymax = max(df["target_branch_pct"].max(), df["target_region_pct"].max()) + 0.25
    ax.set_ylim(ymin, ymax)
    ax.set_title("SQLeek/MySQL Zoomed Coverage", fontsize=11.5, fontweight="bold")
    ax.set_xlabel("Time (h)")
    ax.set_ylabel("Coverage (%)")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")

    ax = axes[1]
    ax.plot(df["hour"], df["target_branch_delta"], color="#d62728", marker="o", linewidth=2.2, label="Target branch gain")
    ax.plot(df["hour"], df["global_branch_delta"], color="#444444", marker="^", linewidth=1.8, linestyle="--", label="Global branch gain")
    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.set_xlim(1, 24)
    ax.set_title("Gain After 1h Checkpoint", fontsize=11.5, fontweight="bold")
    ax.set_xlabel("Time (h)")
    ax.set_ylabel("Additional coverage points")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")

    fig.suptitle("MySQL SQLeek Saturates Early but Still Adds Small Coverage", fontsize=12.5, fontweight="bold", y=1.03)
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.18, top=0.78, wspace=0.28)
    for suffix in ("png", "pdf"):
        fig.savefig(HOUR24_DIR / f"rq2_mysql_sqleek_zoom_delta.{suffix}", dpi=240, bbox_inches="tight")
    plt.close(fig)

    out = df[["elapsed_min", "target_branch_pct", "target_region_pct", "global_branch_pct", "target_branch_delta", "global_branch_delta"]]
    out.to_csv(DATA / "rq2_mysql_sqleek_zoom_delta.csv", index=False)
    print(HOUR24_DIR / "rq2_mysql_sqleek_zoom_delta.png")
    print(DATA / "rq2_mysql_sqleek_zoom_delta.csv")


if __name__ == "__main__":
    main()
