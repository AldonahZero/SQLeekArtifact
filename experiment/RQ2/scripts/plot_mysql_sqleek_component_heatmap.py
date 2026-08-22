#!/usr/bin/env python3
"""Plot the real SQLeek/MySQL component heatmap from replay audit rows."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


BASE_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = BASE_DIR / "result"
AUDIT_CSV = RESULT_DIR / "audit" / "sqleek_mysql" / "component_heatmap_by_run.csv"

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from figure_paths import cleanup_legacy_flat_figures, dbms_heatmap_dir  # noqa: E402

COMPONENT_ORDER = [
    "parser",
    "optimizer",
    "executor",
    "type_system",
    "catalog_metadata",
    "storage",
    "cursor_prepared",
    "other",
]

COMPONENT_LABELS = {
    "parser": "Parser",
    "optimizer": "Optimizer",
    "executor": "Executor",
    "type_system": "Type system",
    "catalog_metadata": "Catalog/metadata",
    "storage": "Storage",
    "cursor_prepared": "Cursor/prepared",
    "other": "Other",
}


def fmt_hit(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def main() -> None:
    if not AUDIT_CSV.exists():
        raise SystemExit(f"missing audit data: {AUDIT_CSV}")
    out_dir = dbms_heatmap_dir("MySQL")
    out_dir.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_flat_figures()

    df = pd.read_csv(AUDIT_CSV)
    df = df[(df["tool"] == "SQLeek") & (df["dbms"].str.lower() == "mysql")]
    if df.empty:
        raise SystemExit("no SQLeek/MySQL rows in component heatmap audit")

    grouped = (
        df.groupby("component", as_index=False)
        .agg(
            branch_hit=("risk_branches_hit", "mean"),
            branch_total=("risk_branches_total", "max"),
            region_hit=("risk_targets_hit", "mean"),
            region_total=("risk_targets_total", "max"),
        )
    )
    grouped["branch_cov"] = grouped["branch_hit"] / grouped["branch_total"]
    grouped["region_cov"] = grouped["region_hit"] / grouped["region_total"]
    grouped = grouped.set_index("component").reindex(COMPONENT_ORDER).dropna(how="all")

    values = pd.DataFrame(
        {
            "Target-region branches": (grouped["branch_cov"] * 100.0).to_numpy(),
            "Target regions": (grouped["region_cov"] * 100.0).to_numpy(),
        },
        index=[COMPONENT_LABELS.get(idx, idx) for idx in grouped.index],
    )
    labels = pd.DataFrame(index=values.index, columns=values.columns, dtype=object)
    for component, row in grouped.iterrows():
        label = COMPONENT_LABELS.get(component, component)
        labels.loc[label, "Target-region branches"] = (
            f"{row['branch_cov'] * 100.0:.1f}%\n"
            f"{fmt_hit(row['branch_hit'])}/{int(row['branch_total'])}"
        )
        labels.loc[label, "Target regions"] = (
            f"{row['region_cov'] * 100.0:.1f}%\n"
            f"{fmt_hit(row['region_hit'])}/{int(row['region_total'])}"
        )

    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    sns.heatmap(
        values,
        ax=ax,
        cmap="YlGnBu",
        vmin=0,
        vmax=max(60.0, float(values.max().max())),
        annot=labels,
        fmt="",
        linewidths=0.7,
        linecolor="white",
        cbar_kws={"label": "Coverage (%)"},
    )
    ax.set_title("SQLeek/MySQL High-Risk Component Coverage", fontsize=13, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(axis="x", rotation=0)
    ax.tick_params(axis="y", rotation=0)
    fig.tight_layout()

    for suffix in ("png", "pdf"):
        fig.savefig(out_dir / f"rq2_mysql_sqleek_component_heatmap.{suffix}", dpi=240, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
