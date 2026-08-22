#!/usr/bin/env python3
"""Summarize the Token--Cost--Bug relationship of multi-model SQLeek runs.

The script reports both raw bug counts and token-normalized yield.  It never
turns a missing bug report or missing usage log into zero, so incomplete runs
cannot create an artificial model advantage.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from result_io import finite_float, read_jsonl, write_csv


RUN_LEVEL_FIELDS = [
    "model_key",
    "model_label",
    "model",
    "dbms",
    "repeat",
    "run_id",
    "run_dir",
    "total_tokens",
    "cost_usd",
    "unique_bug_count",
    "bugs_per_10k_tokens",
    "tokens_per_bug",
]

SUMMARY_FIELDS = [
    "model_key",
    "model_label",
    "dbms",
    "n_runs",
    "total_tokens",
    "total_cost_usd",
    "mean_tokens",
    "median_tokens",
    "mean_bug_count",
    "median_bug_count",
    "min_bug_count",
    "max_bug_count",
    "bug_count_range",
    "bug_count_cv",
    "bugs_per_10k_tokens",
    "tokens_per_bug",
    "pearson_tokens_vs_bugs",
    "spearman_tokens_vs_bugs",
    "correlation_note",
]


def _read_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    if path.suffix.lower() == ".json":
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []
    with path.open("r", encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def _number(row: dict[str, Any], key: str) -> float | None:
    value = finite_float(row.get(key))
    if value is None or value < 0:
        return None
    return value


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _std(values: list[float]) -> float | None:
    return statistics.pstdev(values) if len(values) > 1 else (0.0 if values else None)


def _cv(values: list[float]) -> float | None:
    mean = _mean(values)
    deviation = _std(values)
    if mean in (None, 0) or deviation is None:
        return None
    return deviation / mean


def _pearson(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 3:
        return None
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(y_values)
    x_centered = [value - x_mean for value in x_values]
    y_centered = [value - y_mean for value in y_values]
    denominator = math.sqrt(sum(value * value for value in x_centered) * sum(value * value for value in y_centered))
    if denominator == 0:
        return None
    return sum(x * y for x, y in zip(x_centered, y_centered)) / denominator


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[ordered[position][0]] = rank
        index = end
    return ranks


def _spearman(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 3:
        return None
    return _pearson(_average_ranks(x_values), _average_ranks(y_values))


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _run_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("status", "")).lower() != "completed":
            continue
        tokens = _number(record, "total_tokens")
        cost = _number(record, "cost_usd")
        bugs = _number(record, "unique_bug_count")
        if tokens is None or bugs is None or tokens <= 0:
            continue
        rows.append(
            {
                "model_key": record.get("model_key", ""),
                "model_label": record.get("model_label", record.get("model_key", "")),
                "model": record.get("model", ""),
                "dbms": record.get("dbms", ""),
                "repeat": record.get("repeat", ""),
                "run_id": record.get("run_id", ""),
                "run_dir": record.get("run_dir", ""),
                "cost_usd": _round(cost, 8),
                "total_tokens": int(tokens),
                "unique_bug_count": int(bugs),
                "bugs_per_10k_tokens": _round(bugs / tokens * 10000),
                "tokens_per_bug": _round(tokens / bugs, 2) if bugs > 0 else None,
            }
        )
    return rows


def _summary_row(model_key: str, model_label: str, dbms: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    costs = [float(row["cost_usd"]) for row in rows if row.get("cost_usd") is not None]
    tokens = [float(row["total_tokens"]) for row in rows]
    bugs = [float(row["unique_bug_count"]) for row in rows]
    total_tokens = sum(tokens)
    total_bugs = sum(bugs)
    spearman = _spearman(tokens, bugs)
    pearson = _pearson(tokens, bugs)
    return {
        "model_key": model_key,
        "model_label": model_label,
        "dbms": dbms,
        "total_cost_usd": _round(sum(costs), 8) if len(costs) == len(rows) else None,
        "n_runs": len(rows),
        "total_tokens": int(total_tokens),
        "mean_tokens": _round(_mean(tokens), 2),
        "median_tokens": _round(_median(tokens), 2),
        "mean_bug_count": _round(_mean(bugs), 4),
        "median_bug_count": _round(_median(bugs), 4),
        "min_bug_count": int(min(bugs)),
        "max_bug_count": int(max(bugs)),
        "bug_count_range": int(max(bugs) - min(bugs)),
        "bug_count_cv": _round(_cv(bugs)),
        "bugs_per_10k_tokens": _round(total_bugs / total_tokens * 10000) if total_tokens else None,
        "tokens_per_bug": _round(total_tokens / total_bugs, 2) if total_bugs else None,
        "pearson_tokens_vs_bugs": _round(pearson),
        "spearman_tokens_vs_bugs": _round(spearman),
        "correlation_note": "run-level correlation; requires >=3 non-identical observations",
    }


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No complete runs with both token usage and bug count._\n"
    header = "| " + " | ".join(columns) + " |\n"
    separator = "| " + " | ".join("---" for _ in columns) + " |\n"
    body = "".join("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |\n" for row in rows)
    return header + separator + body


def build_report(run_rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> str:
    model_rows = [row for row in summary_rows if row["dbms"] == "ALL"]
    mean_counts = [
        (str(row["model_label"]), float(row["mean_bug_count"]))
        for row in model_rows
        if row.get("mean_bug_count") is not None
    ]
    if mean_counts:
        lowest_label, lowest_count = min(mean_counts, key=lambda item: item[1])
        highest_label, highest_count = max(mean_counts, key=lambda item: item[1])
        if lowest_count > 0:
            spread = f"{highest_count / lowest_count:.2f}x"
        else:
            spread = "undefined because the lowest mean is zero"
        spread_note = (
            f"Across model-level means, Bug counts range from {lowest_count:.2f} ({lowest_label}) "
            f"to {highest_count:.2f} ({highest_label}); the max/min spread is {spread}."
        )
    else:
        spread_note = "No model-level Bug-count spread can be computed yet."
    lines = [
        "# Multi-model Token–Cost–Bug analysis",
        "",
        "Only runs with `status=completed`, a provider-reported `total_tokens`, and a deduplicated `unique_bug_count` are included.",
        "Cost is estimated only when provider-reported prompt/completion tokens and configured input/output rates are available.",
        "",
        "## Model-level comparison",
        "",
        _markdown_table(
            model_rows,
            [
                "model_label",
                "n_runs",
                "total_tokens",
                "total_cost_usd",
                "mean_bug_count",
                "median_bug_count",
                "min_bug_count",
                "max_bug_count",
                "bug_count_cv",
                "bugs_per_10k_tokens",
                "tokens_per_bug",
                "spearman_tokens_vs_bugs",
            ],
        ),
        "",
        spread_note,
        "",
        "## Interpretation guardrails",
        "",
        "- Compare models under the same DBMS, seed limits, fuzzing duration, repeat count, and bug deduplication rule.",
        "- A higher raw Bug count is not sufficient evidence of a better model; inspect `bugs_per_10k_tokens` and the run-level correlation.",
        "- `bug_count_cv`, min/max counts, and per-DBMS rows expose instability such as a drop from tens of Bugs to single digits.",
        "- A correlation value is intentionally left blank when fewer than three runs or no variation is available; do not infer a trend from one run per model.",
        "",
        "## Included run-level observations",
        "",
        f"Complete observations: **{len(run_rows)}**.",
        "",
        _markdown_table(
            run_rows,
            ["model_label", "dbms", "repeat", "total_tokens", "cost_usd", "unique_bug_count", "bugs_per_10k_tokens", "tokens_per_bug"],
        ),
    ]
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None, help="results.jsonl/csv; default: experiment/otherLLMs/results/results.jsonl")
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    input_path = (args.input or script_dir / "results" / "results.jsonl").expanduser().resolve()
    output_dir = (args.output_dir or input_path.parent / "analysis").expanduser().resolve()
    records = _read_records(input_path)
    run_rows = _run_rows(records)

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in run_rows:
        model_key = str(row["model_key"])
        model_label = str(row["model_label"])
        dbms = str(row["dbms"])
        labels[model_key] = model_label
        grouped[(model_key, model_label, dbms)].append(row)

    summary_rows: list[dict[str, Any]] = []
    for (model_key, model_label, dbms), rows in sorted(grouped.items()):
        summary_rows.append(_summary_row(model_key, model_label, dbms, rows))

    for model_key, model_label in sorted(labels.items()):
        rows = [row for row in run_rows if row["model_key"] == model_key]
        summary_rows.append(_summary_row(model_key, model_label, "ALL", rows))

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "run_level_token_bug.csv", run_rows, fieldnames=RUN_LEVEL_FIELDS)
    write_csv(output_dir / "summary_token_bug.csv", summary_rows, fieldnames=SUMMARY_FIELDS)
    (output_dir / "analysis.md").write_text(build_report(run_rows, summary_rows), encoding="utf-8")
    print(f"complete observations: {len(run_rows)}")
    print(f"wrote: {output_dir / 'summary_token_bug.csv'}")
    print(f"wrote: {output_dir / 'analysis.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
