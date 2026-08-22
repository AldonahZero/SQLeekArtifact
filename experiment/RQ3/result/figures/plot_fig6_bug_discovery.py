#!/usr/bin/env python3
"""Plot Figure 6 bug-discovery curves from event-level bug data."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd


TOOLS = ["SQLeek", "SQUIRREL", "Griffin", "SQLaser"]
DBMS_ORDER = ["PostgreSQL", "MySQL", "MariaDB", "MonetDB"]
RUNS = ["r1", "r2", "r3", "r4", "r5"]
REQUIRED_COLUMNS = ["tool", "dbms", "run", "bug_id", "first_seen_hours"]
ZERO_RUN_MARKER = "__NO_BUG__"

# These pairs are omitted instead of being plotted as zero-valued curves.
UNSUPPORTED_PAIRS = {
    ("SQLaser", "MariaDB"),
    ("SQLaser", "MonetDB"),
    ("SQUIRREL", "MonetDB"),
}

STYLE = {
    "SQLeek": {
        "color": "#E69F00",
        "linestyle": "-",
        "linewidth": 1.75,
    },
    "SQUIRREL": {
        "color": "#009E73",
        "linestyle": (0, (4.8, 3.0)),
        "linewidth": 1.22,
    },
    "Griffin": {
        "color": "#7E57C2",
        "linestyle": (0, (1.0, 1.8)),
        "linewidth": 1.20,
    },
    "SQLaser": {
        "color": "#7F7F7F",
        "linestyle": (0, (2.2, 2.2)),
        "linewidth": 1.18,
    },
}

SUBPLOT_TITLES = {
    "PostgreSQL": "(a) PostgreSQL",
    "MySQL": "(b) MySQL",
    "MariaDB": "(c) MariaDB",
    "MonetDB": "(d) MonetDB",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Figure 6 cumulative unique bug discovery curves."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Event-level CSV with tool,dbms,run,bug_id,first_seen_hours.",
    )
    parser.add_argument(
        "--output-prefix",
        default="Figure6_Bug_Discovery_Over_Time",
        help="Output prefix without extension.",
    )
    parser.add_argument(
        "--allow-partial-runs",
        action="store_true",
        help="Allow observed tool-DBMS pairs to contain fewer than r1-r5 runs.",
    )
    parser.add_argument(
        "--stats-output",
        default=None,
        help="Optional CSV path for per-checkpoint mean/std statistics.",
    )
    parser.add_argument(
        "--zero-missing-runs-for",
        action="append",
        default=[],
        help=(
            "Tool:DBMS pair whose absent r1-r5 event rows should be counted as "
            "zero-bug runs, e.g., SQLeek:PostgreSQL. May be repeated."
        ),
    )
    return parser.parse_args()


def resolve_input_path(raw_path: str) -> Path:
    input_path = Path(raw_path)
    if input_path.is_absolute():
        return input_path

    cwd_candidate = Path.cwd() / input_path
    if cwd_candidate.exists():
        return cwd_candidate

    script_candidate = Path(__file__).resolve().parent / input_path
    return script_candidate


def resolve_output_prefix(raw_prefix: str) -> Path:
    output_prefix = Path(raw_prefix)
    if output_prefix.parent != Path("."):
        output_prefix.parent.mkdir(parents=True, exist_ok=True)
    return output_prefix


def parse_zero_missing_pairs(raw_specs: list[str]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for raw_spec in raw_specs:
        for spec in raw_spec.split(","):
            spec = spec.strip()
            if not spec:
                continue
            if ":" not in spec:
                raise ValueError(
                    "--zero-missing-runs-for expects Tool:DBMS, "
                    f"got {spec!r}."
                )
            tool, dbms = [part.strip() for part in spec.split(":", 1)]
            if tool not in TOOLS:
                raise ValueError(f"Unknown tool in --zero-missing-runs-for: {tool}")
            if dbms not in DBMS_ORDER:
                raise ValueError(f"Unknown DBMS in --zero-missing-runs-for: {dbms}")
            if (tool, dbms) in UNSUPPORTED_PAIRS:
                raise ValueError(
                    "Unsupported pair in --zero-missing-runs-for: "
                    f"{tool}:{dbms}"
                )
            pairs.add((tool, dbms))
    return pairs


def validate_input(df: pd.DataFrame, input_path: Path, allow_partial_runs: bool) -> pd.DataFrame:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"{input_path} is missing required columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    if df.empty:
        raise ValueError(f"{input_path} contains no bug events.")

    for column in ["tool", "dbms", "run"]:
        if df[column].isna().any():
            raise ValueError(f"{input_path} contains missing values in column {column}.")
        df[column] = df[column].astype(str).str.strip()
        if (df[column] == "").any():
            raise ValueError(f"{input_path} contains empty values in column {column}.")
    df["bug_id"] = df["bug_id"].fillna("").astype(str).str.strip()
    df.loc[df["bug_id"] == "", "bug_id"] = ZERO_RUN_MARKER

    try:
        df["first_seen_hours"] = pd.to_numeric(df["first_seen_hours"])
    except Exception as exc:
        raise ValueError("first_seen_hours must be numeric.") from exc

    if not np.isfinite(df["first_seen_hours"]).all():
        raise ValueError("first_seen_hours contains non-finite values.")
    outside_budget = df[(df["first_seen_hours"] < 0) | (df["first_seen_hours"] > 24)]
    if not outside_budget.empty:
        raise ValueError(
            "first_seen_hours must be within 0-24. Bad rows:\n"
            + outside_budget.to_string(index=False)
        )

    unknown_tools = sorted(set(df["tool"]) - set(TOOLS))
    if unknown_tools:
        raise ValueError(f"Unknown tools in input: {unknown_tools}")

    unknown_dbms = sorted(set(df["dbms"]) - set(DBMS_ORDER))
    if unknown_dbms:
        raise ValueError(f"Unknown DBMS names in input: {unknown_dbms}")

    unknown_runs = sorted(set(df["run"]) - set(RUNS))
    if unknown_runs:
        raise ValueError(f"run must be one of {RUNS}. Unknown runs: {unknown_runs}")

    pair_frame = df[["tool", "dbms"]].drop_duplicates()
    unsupported_present = [
        (row.tool, row.dbms)
        for row in pair_frame.itertuples(index=False)
        if (row.tool, row.dbms) in UNSUPPORTED_PAIRS
    ]
    if unsupported_present:
        raise ValueError(
            "Input contains unsupported tool-DBMS configurations that should be omitted: "
            + str(sorted(unsupported_present))
        )

    duplicate_mask = df.duplicated(["tool", "dbms", "run", "bug_id"], keep=False)
    if duplicate_mask.any():
        duplicate_rows = df.loc[duplicate_mask].sort_values(
            ["tool", "dbms", "run", "bug_id", "first_seen_hours"]
        )
        raise ValueError(
            "Duplicate bug_id values within the same tool/dbms/run:\n"
            + duplicate_rows.to_string(index=False)
        )

    expected_pairs = {
        (tool, dbms)
        for tool in TOOLS
        for dbms in DBMS_ORDER
        if (tool, dbms) not in UNSUPPORTED_PAIRS
    }
    observed_pairs = set(zip(df["tool"], df["dbms"]))
    missing_supported_pairs = sorted(expected_pairs - observed_pairs)
    if missing_supported_pairs and not allow_partial_runs:
        raise ValueError(
            "Supported tool-DBMS configurations have no rows. "
            "If they are unsupported, add them to UNSUPPORTED_PAIRS; otherwise provide r1-r5 events: "
            + str(missing_supported_pairs)
        )

    missing_runs = []
    for tool, dbms in sorted(observed_pairs):
        runs = set(df.loc[(df["tool"] == tool) & (df["dbms"] == dbms), "run"])
        absent = sorted(set(RUNS) - runs)
        if absent:
            missing_runs.append((tool, dbms, absent))
    if missing_runs and not allow_partial_runs:
        raise ValueError(f"Observed tool-DBMS configurations are missing runs: {missing_runs}")

    return df.sort_values(["dbms", "tool", "run", "first_seen_hours", "bug_id"]).reset_index(
        drop=True
    )


def make_checkpoints(df: pd.DataFrame) -> np.ndarray:
    del df
    return np.array([1, 5, 10, 15, 20, 24], dtype=float)


def summarize_pair(
    pair_df: pd.DataFrame,
    checkpoints: np.ndarray,
    allow_partial_runs: bool,
    zero_missing_runs: bool,
) -> pd.DataFrame:
    run_counts = []
    runs = RUNS
    if allow_partial_runs and not zero_missing_runs:
        observed_runs = set(pair_df["run"])
        runs = [run for run in RUNS if run in observed_runs]
    if not runs:
        raise ValueError("Cannot summarize a tool-DBMS pair with no runs.")

    for run in runs:
        run_df = pair_df[pair_df["run"] == run]
        event_df = run_df[run_df["bug_id"] != ZERO_RUN_MARKER]
        event_times = np.sort(event_df["first_seen_hours"].to_numpy(dtype=float))
        counts = np.searchsorted(event_times, checkpoints, side="right")
        if np.any(np.diff(counts) < 0):
            raise ValueError(f"Non-monotonic cumulative counts found for run {run}.")
        run_counts.append(counts)

    counts_by_run = np.vstack(run_counts)
    mean = counts_by_run.mean(axis=0)
    minimum = counts_by_run.min(axis=0)
    maximum = counts_by_run.max(axis=0)
    if len(runs) > 1:
        std = counts_by_run.std(axis=0, ddof=1)
    else:
        std = np.zeros_like(mean)
    if np.any(np.diff(mean) < -1e-9):
        raise ValueError("Non-monotonic mean cumulative counts found.")

    return pd.DataFrame(
        {
            "time": checkpoints,
            "mean": mean,
            "std": std,
            "min": minimum,
            "max": maximum,
            "n_runs": len(runs),
        }
    )


def build_all_stats(
    df: pd.DataFrame,
    checkpoints: np.ndarray,
    allow_partial_runs: bool,
    zero_missing_pairs: set[tuple[str, str]],
) -> dict[tuple[str, str], pd.DataFrame]:
    stats = {}
    for dbms in DBMS_ORDER:
        for tool in TOOLS:
            pair_df = df[(df["dbms"] == dbms) & (df["tool"] == tool)]
            if pair_df.empty:
                continue
            zero_missing_runs = (tool, dbms) in zero_missing_pairs
            stats[(dbms, tool)] = summarize_pair(
                pair_df, checkpoints, allow_partial_runs, zero_missing_runs
            )
    return stats


def write_stats_csv(
    stats: dict[tuple[str, str], pd.DataFrame],
    output_path: Path,
) -> None:
    rows = []
    for dbms in DBMS_ORDER:
        for tool in TOOLS:
            series = stats.get((dbms, tool))
            if series is None:
                continue
            for row in series.itertuples(index=False):
                rows.append(
                    {
                        "dbms": dbms,
                        "tool": tool,
                        "checkpoint_hours": float(row.time),
                        "n_runs": int(row.n_runs),
                        "mean": float(row.mean),
                        "sample_std": float(row.std),
                        "min": float(row.min),
                        "max": float(row.max),
                    }
                )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_path, index=False)


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Arial",
                "Helvetica",
                "Nimbus Sans",
                "Liberation Sans",
                "DejaVu Sans",
            ],
            "font.size": 8.7,
            "axes.titlesize": 9.4,
            "axes.labelsize": 8.7,
            "xtick.labelsize": 8.0,
            "ytick.labelsize": 8.0,
            "legend.fontsize": 8.0,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def compute_y_ranges(stats: dict[tuple[str, str], pd.DataFrame]) -> dict[str, tuple[float, float]]:
    ranges = {}
    for dbms in DBMS_ORDER:
        upper = 0.0
        for tool in TOOLS:
            series = stats.get((dbms, tool))
            if series is None:
                continue
            upper = max(upper, float(series["max"].max()))
        y_max = max(1.0, float(math.ceil(upper)))
        ranges[dbms] = (0.0, y_max)
    return ranges


def add_zero_anchor(series: pd.DataFrame) -> pd.DataFrame:
    """Add a visual 0h anchor so step plots do not imply bugs at campaign start."""
    if np.isclose(series["time"], 0.0).any():
        return series
    anchor = pd.DataFrame(
        [
            {
                "time": 0.0,
                "mean": 0.0,
                "std": 0.0,
                "min": 0.0,
                "max": 0.0,
                "n_runs": int(series["n_runs"].iloc[0]),
            }
        ]
    )
    return pd.concat([anchor, series], ignore_index=True)


def should_plot_series(series: pd.DataFrame) -> bool:
    """Omit tool-DBMS curves that discovered no bugs in every run."""
    return float(series["max"].max()) > 0.0


def display_mean(series: pd.DataFrame, tool: str) -> np.ndarray:
    """Return the exact mean values plotted for a tool."""
    del tool
    return series["mean"].to_numpy(dtype=float)


def plot_figure(
    stats: dict[tuple[str, str], pd.DataFrame],
    output_prefix: Path,
) -> dict[str, tuple[float, float]]:
    configure_matplotlib()
    y_ranges = compute_y_ranges(stats)
    fig, axes = plt.subplots(2, 2, figsize=(7.15, 4.55), sharex=False, sharey=False)
    axes = axes.flatten()
    line_order = [tool for tool in TOOLS if tool != "SQLeek"] + ["SQLeek"]
    plotted_tools: set[str] = set()

    for ax, dbms in zip(axes, DBMS_ORDER):
        for tool in TOOLS:
            series = stats.get((dbms, tool))
            if series is None or not should_plot_series(series):
                continue
            plot_series = add_zero_anchor(series)
            style = STYLE[tool]
            ax.fill_between(
                plot_series["time"],
                plot_series["min"],
                plot_series["max"],
                step="post",
                color=style["color"],
                alpha=0.050 if tool == "SQLeek" else 0.030,
                linewidth=0,
                zorder=1,
            )

        for tool in line_order:
            series = stats.get((dbms, tool))
            if series is None or not should_plot_series(series):
                continue
            plot_series = add_zero_anchor(series)
            style = STYLE[tool]
            ax.step(
                plot_series["time"],
                display_mean(plot_series, tool),
                where="post",
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=style["linewidth"],
                label=tool,
                solid_capstyle="round",
                solid_joinstyle="round",
                dash_capstyle="round",
                dash_joinstyle="round",
                zorder=4 if tool == "SQLeek" else 3,
            )
            plotted_tools.add(tool)

        ax.set_title(SUBPLOT_TITLES[dbms], fontweight="semibold", pad=4)
        ax.set_xlabel("Time (h)")
        ax.set_ylabel("Cumulative unique bugs")
        ax.set_xlim(0, 24)
        ax.set_ylim(*y_ranges[dbms])
        ax.set_xticks([0, 5, 10, 15, 20, 24])
        ax.set_xticklabels(["0h", "5h", "10h", "15h", "20h", "24h"])
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        ax.grid(axis="y", color="#D6D6D6", linewidth=0.45, alpha=0.70)
        ax.grid(axis="x", visible=False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_linewidth(0.68)
        ax.spines["bottom"].set_linewidth(0.68)
        ax.tick_params(width=0.72, length=2.8)

    handles = [
        Line2D(
            [0],
            [0],
            color=STYLE[tool]["color"],
            linestyle=STYLE[tool]["linestyle"],
            linewidth=STYLE[tool]["linewidth"],
            label=tool,
        )
        for tool in TOOLS
        if tool in plotted_tools
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.012),
        ncol=max(1, len(handles)),
        frameon=False,
        handlelength=2.45,
        columnspacing=0.85,
        handletextpad=0.38,
    )
    fig.subplots_adjust(left=0.083, right=0.99, top=0.955, bottom=0.18, wspace=0.27, hspace=0.40)

    pdf_path = output_prefix.with_suffix(".pdf")
    svg_path = output_prefix.with_suffix(".svg")
    png_path = output_prefix.with_suffix(".png")

    fig.savefig(pdf_path, bbox_inches="tight", pad_inches=0.025)
    fig.savefig(svg_path, bbox_inches="tight", pad_inches=0.025)

    fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0.025)
    plt.close(fig)
    return y_ranges


def print_summary(
    stats: dict[tuple[str, str], pd.DataFrame],
    y_ranges: dict[str, tuple[float, float]],
) -> None:
    print("Generated Figure 6 bug-discovery curves.")
    print("Y_AXIS_RANGES")
    for dbms in DBMS_ORDER:
        ymin, ymax = y_ranges[dbms]
        print(f"{dbms},{ymin:g},{ymax:g}")

    print("SUMMARY_24H")
    print("dbms,tool,mean,std,min,max")
    for dbms in DBMS_ORDER:
        for tool in TOOLS:
            series = stats.get((dbms, tool))
            if series is None:
                print(f"{dbms},{tool},N/A,N/A,N/A,N/A")
                continue
            final_row = series.loc[np.isclose(series["time"], 24.0)].iloc[-1]
            print(
                f"{dbms},{tool},{final_row['mean']:.3f},{final_row['std']:.3f},"
                f"{final_row['min']:.3f},{final_row['max']:.3f}"
            )


def main() -> None:
    args = parse_args()
    input_path = resolve_input_path(args.input)
    output_prefix = resolve_output_prefix(args.output_prefix)
    zero_missing_pairs = parse_zero_missing_pairs(args.zero_missing_runs_for)

    df = pd.read_csv(input_path, comment="#")
    df = validate_input(df, input_path, args.allow_partial_runs)
    checkpoints = make_checkpoints(df)
    stats = build_all_stats(df, checkpoints, args.allow_partial_runs, zero_missing_pairs)
    y_ranges = plot_figure(stats, output_prefix)

    if args.stats_output:
        stats_output = Path(args.stats_output)
        if not stats_output.is_absolute():
            stats_output = output_prefix.parent / stats_output
        write_stats_csv(stats, stats_output)
        print(f"STATS_OUTPUT {stats_output}")

    for suffix in [".pdf", ".png", ".svg"]:
        print(f"OUTPUT {output_prefix.with_suffix(suffix)}")
    print_summary(stats, y_ranges)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
