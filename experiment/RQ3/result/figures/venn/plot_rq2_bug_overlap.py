#!/usr/bin/env python3
"""Draw the RQ2 bug-overlap Euler diagram."""

import argparse
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Circle


OUTPUT_STEM = Path(__file__).resolve().parent / "rq2_bug_overlap_venn"

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
    "DynSQL": {"color": "#0072B2", "linestyle": "-.", "linewidth": 0.95},
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


def add_count(ax, x, y, value, fontsize=8.8):
    ax.text(
        x,
        y,
        str(value),
        ha="center",
        va="center",
        fontsize=fontsize,
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

    # Counts are disjoint membership regions, not area-proportional.
    # DynSQL counts by DBMS: PostgreSQL=0, MySQL=3, MariaDB=4, MonetDB=4.
    # User-confirmed: D subset Q; |D&G|=3; |D&S|=1;
    # |D&G&S|=1; |D&SQLaser|=0. Other intersections are unchanged.
    circles = {
        "SQLeek": ((0.0, 0.0), 1.00),
        "Griffin": ((-0.22, -0.45), 0.62),
        "SQUIRREL": ((0.28, -0.52), 0.55),
        "DynSQL": ((-0.30, 0.12), 0.48),
        "SQLaser": ((0.45, 0.30), 0.18),
    }
    regions = [
        ({"SQLeek"}, 37, (0.20, 0.77)),
        ({"SQLeek", "SQLaser"}, 1, (0.45, 0.30)),
        ({"SQLeek", "SQUIRREL"}, 1, (0.55, -0.42)),
        ({"SQLeek", "Griffin", "SQUIRREL"}, 1, (0.03, -0.60)),
        ({"SQLeek", "Griffin"}, 5, (-0.52, -0.56)),
        ({"SQUIRREL"}, 1, (0.52, -0.935)),
        ({"Griffin"}, 2, (-0.45, -0.96)),
        ({"SQLeek", "DynSQL"}, 8, (-0.30, 0.35)),
        ({"SQLeek", "DynSQL", "Griffin"}, 2, (-0.45, -0.08)),
        ({"SQLeek", "DynSQL", "Griffin", "SQUIRREL"}, 1, (-0.035, -0.175)),
    ]
    expected = {"SQLeek": 56, "Griffin": 11, "SQUIRREL": 4,
                "DynSQL": 11, "SQLaser": 1}
    for tool, total in expected.items():
        assert sum(n for members, n, _ in regions if tool in members) == total
    for others, expected_overlap in [({"Griffin"}, 3), ({"SQUIRREL"}, 1),
                                     ({"Griffin", "SQUIRREL"}, 1), ({"SQLaser"}, 0)]:
        assert sum(n for members, n, _ in regions
                   if others | {"DynSQL"} <= members) == expected_overlap
    assert all("SQLeek" in members for members, _, _ in regions if "DynSQL" in members)
    for tool, (center, radius) in circles.items():
        add_circle(ax, center, radius, tool)
    for members, count, (x, y) in regions:
        actual = {tool for tool, ((cx, cy), radius) in circles.items()
                  if (x-cx)**2 + (y-cy)**2 < radius**2}
        assert actual == members, (members, actual)
        add_count(ax, x, y, count, fontsize=7.5 if "SQLeek" not in members else 8.8)

    legend_x = 1.28
    legend_y = [0.72, 0.36, 0.0, -0.36, -0.72]
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
