#!/usr/bin/env python3
"""Plot total LLM tokens against deduplicated Bugs for completed runs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None, help="run_level_token_bug.csv")
    parser.add_argument("--output", type=Path, default=None, help="PNG path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    input_path = (args.input or script_dir / "results" / "analysis" / "run_level_token_bug.csv").expanduser().resolve()
    output_path = (args.output or input_path.with_name("token_bug_tradeoff.png")).expanduser().resolve()

    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError as exc:
        raise SystemExit("matplotlib is required for the optional plot: pip install matplotlib") from exc

    with input_path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if not rows:
        raise SystemExit(f"no complete run-level rows found in {input_path}")

    labels = sorted({row.get("model_label", row.get("model_key", "")) for row in rows})
    colors = plt.get_cmap("tab10")
    fig, ax = plt.subplots(figsize=(7.2, 5.0), constrained_layout=True)
    for index, label in enumerate(labels):
        selected = [row for row in rows if row.get("model_label", row.get("model_key", "")) == label]
        x = [int(row["total_tokens"]) for row in selected]
        y = [int(row["unique_bug_count"]) for row in selected]
        ax.scatter(x, y, label=label, s=60, alpha=0.85, color=colors(index % 10))

    ax.set_xscale("log")
    ax.set_xlabel("Total LLM tokens (log scale)")
    ax.set_ylabel("Deduplicated Bugs found")
    ax.set_title("SQLeek multi-model Token–Bug trade-off")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(frameon=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220)
    print(f"wrote: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
