#!/usr/bin/env python3
"""Draw the RQ2 bug-overlap Euler diagram."""

import argparse
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Circle


PAPER_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_STEM = PAPER_ROOT / "media" / "RQ2_Bug_Overlap_Venn"

STYLES = {
    "SQLeek": {"color": "#E69F00", "linestyle": "-", "linewidth": 0.95},
    "SQUIRREL": {
        "color": "#009E73",
        "linestyle": (0, (4.8, 3.0)),
        "linewidth": 0.85,
    },
    "Griffin": {
        "color": "#7E57C2",
        "linestyle": (0, (1.0, 1.8)),
        "linewidth": 0.85,
    },
    "SQLaser": {
        "color": "#7F7F7F",
        "linestyle": (0, (2.2, 2.2)),
        "linewidth": 0.80,
    },
}


def add_circle(ax, center, radius, tool):
    style = STYLES[tool]
    ax.add_patch(
        Circle(
            center,
            radius,
            fill=False,
            edgecolor=style["color"],
            linestyle=style["linestyle"],
            linewidth=style["linewidth"],
        )
    )


def add_count(ax, x, y, value):
    ax.text(
        x,
        y,
        str(value),
        ha="center",
        va="center",
        fontsize=8.8,
        fontweight="bold",
        color="black",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Draw the RQ2 bug-overlap Euler diagram."
    )
    parser.add_argument(
        "--output-stem",
        type=Path,
        default=OUTPUT_STEM,
        help="Output path without an extension.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_stem = args.output_stem.expanduser().resolve()
    output_stem.parent.mkdir(parents=True, exist_ok=True)

    mpl.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )

    fig, ax = plt.subplots(figsize=(3.35, 2.15))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Match the compact DynSQL-style composition: SQLeek is the large set,
    # while the baseline sets cluster in its lower half.
    add_circle(ax, (0.0, 0.0), 1.00, "SQLeek")
    add_circle(ax, (-0.22, -0.45), 0.62, "Griffin")
    add_circle(ax, (0.28, -0.52), 0.55, "SQUIRREL")
    add_circle(ax, (0.45, 0.30), 0.18, "SQLaser")

    # Mutually exclusive regions:
    # SQLeek only; SQLaser∩SQLeek; SQUIRREL∩SQLeek only;
    # SQUIRREL∩Griffin∩SQLeek; Griffin∩SQLeek only;
    # SQUIRREL only; Griffin only.
    add_count(ax, -0.36, 0.38, 45)
    add_count(ax, 0.45, 0.30, 1)
    add_count(ax, 0.50, -0.29, 1)
    add_count(ax, 0.03, -0.50, 2)
    add_count(ax, -0.40, -0.23, 7)
    add_count(ax, 0.48, -0.97, 1)
    add_count(ax, -0.50, -0.96, 2)

    legend_x = 1.28
    legend_y = [0.56, 0.19, -0.18, -0.55]
    for tool, y in zip(STYLES, legend_y):
        add_circle(ax, (legend_x, y), 0.075, tool)
        ax.text(
            legend_x + 0.12,
            y,
            tool,
            ha="left",
            va="center",
            fontsize=7.2,
            fontweight="bold",
            color="black",
        )

    ax.set_xlim(-1.06, 2.18)
    ax.set_ylim(-1.14, 1.05)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    for suffix in ("pdf", "png", "svg"):
        kwargs = {"bbox_inches": "tight", "pad_inches": 0.02}
        if suffix == "png":
            kwargs["dpi"] = 400
        fig.savefig(output_stem.with_suffix(f".{suffix}"), **kwargs)
    plt.close(fig)


if __name__ == "__main__":
    main()
