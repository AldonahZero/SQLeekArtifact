#!/usr/bin/env python3
"""Generate markdown reports and Excel summary for Observation 1."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from common import (
    canonical_dbms,
    data_dir,
    exp_dir,
    read_csv,
    report_dir,
    results_dir,
    truthy,
    write_markdown,
    write_xlsx,
)


def result_dir(dbms: str) -> Path:
    return exp_dir() / "results" / "combined" if dbms == "combined" else results_dir(dbms)


def count_rows(path: Path) -> int:
    return len(read_csv(path))


def group_row(dbms: str, group: str) -> dict[str, str]:
    for row in read_csv(result_dir(dbms) / "group_statistics.csv"):
        if row.get("group") == group:
            return row
    return {}


def comparison_row(dbms: str, comparison: str) -> dict[str, str]:
    for row in read_csv(result_dir(dbms) / "group_comparisons.csv"):
        if row.get("comparison") == comparison:
            return row
    return {}


def fmt_pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "NA"


def fmt_float(value: object, digits: int = 2) -> str:
    try:
        v = float(value)
        if math.isnan(v):
            return "NA"
        return f"{v:.{digits}f}"
    except Exception:
        return "NA"


def fmt_p(value: object) -> str:
    try:
        v = float(value)
        if math.isnan(v):
            return "NA"
        if v < 0.001:
            return "<0.001"
        return f"{v:.3f}"
    except Exception:
        return "NA"


def data_quality_for(dbms: str) -> dict[str, object]:
    bugs = read_csv(data_dir(dbms) / "bug_fix_commits.csv")
    mappings = read_csv(data_dir(dbms) / "bug_region_mapping.csv")
    dataset = [r for r in read_csv(data_dir(dbms) / "risk_region_dataset.csv") if truthy(r.get("primary_window", ""))]
    coverage_missing = sum(1 for r in dataset if truthy(r.get("coverage_missing", "")) or r.get("coverage_source") == "missing_official_test_coverage")
    reach_missing = sum(1 for r in dataset if r.get("reachability_method") in {"missing", "not_found_in_stage1_sql_entry_callchains"})
    method_counts: dict[str, int] = {}
    for row in mappings:
        method = row.get("mapping_method", "")
        method_counts[method] = method_counts.get(method, 0) + 1
    return {
        "dbms": dbms,
        "candidate_bug_fix_commits": len(bugs),
        "high_confidence_bug_fixes": sum(1 for r in bugs if r.get("confidence") == "high"),
        "unique_bugs": count_rows(data_dir(dbms) / "unique_bugs.csv"),
        "risk_regions": count_rows(data_dir(dbms) / "risk_regions.csv"),
        "mapping_rows": len(mappings),
        "mapping_exact_overlap": method_counts.get("exact_overlap", 0),
        "mapping_current_coordinate_overlap": method_counts.get("current_coordinate_overlap", 0),
        "mapping_partial_overlap": method_counts.get("partial_overlap", 0),
        "mapping_function_context": method_counts.get("function_context", 0),
        "mapping_line_history_skipped": method_counts.get("line_history_skipped", 0),
        "mapping_fuzzy": method_counts.get("fuzzy", 0),
        "mapping_unmapped": method_counts.get("unmapped", 0),
        "coverage_missing_regions": coverage_missing,
        "sql_reachability_missing_regions": reach_missing,
        "validation_positive_regions": sum(1 for r in dataset if truthy(r.get("future_bug_fixed", ""))),
        "primary_sample_count": len(dataset),
    }


def methodology_report() -> None:
    body = """
This experiment validates Observation 1 at the **risk-region** level for MySQL and PostgreSQL.

Risk regions are built from SQLeek Stage 1 CodeQL source ranges, not from whole functions or files. The region identifier is stable and reproducible from DBMS, normalized file path, start line, end line, and Stage 1 rule id. Enclosing function and file/component information are retained only as metadata and control variables.

The causal order is:

1. Define historical and validation windows from the actual Git history.
2. Use only the historical window to compute historical repair signal, official-test coverage gap signal, and SQL-entry reachability signal.
3. Label each historical risk region by independent high-confidence bug-fix commits in the future validation window.

Backports and supplementary commits for bugs first fixed in the historical window are excluded from future labels. The main bug-region mapping uses high-confidence `git log -L` line-history overlap with the Stage 1 source range. Current-line overlap and function-context matches are retained only as sensitivity evidence.

Official coverage is intentionally separated from fuzzing/replay coverage. If an official-test coverage CSV is not configured, MySQL uses a lightweight static proxy over the official `mysql-test/*.test` suite: each test file name/suite path and rare SQL-content tokens are matched against Stage 1 region path, enclosing-function, component, and alert tokens with IDF weighting. This proxy is recorded as `mysql_static_official_test_suite_proxy:mysql-test/*.test` and should not be described as dynamic LLVM line coverage. For DBMSes without either an official coverage CSV or a test-suite proxy, coverage is marked missing and the main coverage-gap signal is not inferred from SQLeek/RQ2 fuzzing coverage.
""".strip()
    write_markdown(report_dir() / "methodology.md", "Observation 1 Methodology", [("Method", body)])


def data_quality_report() -> list[dict[str, object]]:
    rows = [data_quality_for("mysql"), data_quality_for("postgresql")]
    combined = {"dbms": "combined"}
    for key in rows[0].keys():
        if key == "dbms":
            continue
        combined[key] = sum(int(r.get(key, 0) or 0) for r in rows)
    rows.append(combined)
    lines = [
        "| DBMS | Candidates | High-confidence | Unique bugs | Risk regions | Exact mappings | Current-coordinate mappings | Line-history skipped | Unmapped | Missing coverage | Missing reachability | Future-positive regions |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['dbms']} | {row['candidate_bug_fix_commits']} | {row['high_confidence_bug_fixes']} | {row['unique_bugs']} | {row['risk_regions']} | {row['mapping_exact_overlap']} | {row['mapping_current_coordinate_overlap']} | {row['mapping_line_history_skipped']} | {row['mapping_unmapped']} | {row['coverage_missing_regions']} | {row['sql_reachability_missing_regions']} | {row['validation_positive_regions']} |"
        )
    caveat = (
        "\n\nIf future-positive regions are very sparse, the report emphasizes effect sizes and confidence intervals rather than claiming strong significance. "
        "Coverage-missing rows indicate that no official-test coverage artifact was available to populate the primary coverage-gap signal. "
        "Current-coordinate mappings are a fast statistical approximation and should be separated from the strict exact-overlap line-history analysis."
    )
    write_markdown(report_dir() / "data_quality.md", "Data Quality", [("Summary", "\n".join(lines) + caveat)])
    return rows


def dbms_result_report(dbms: str) -> None:
    hcs = group_row(dbms, "H+C+S")
    none = group_row(dbms, "None")
    comp = comparison_row(dbms, "H+C+S vs None")
    groups = read_csv(result_dir(dbms) / "group_statistics.csv")
    group_lines = [
        "| Group | Regions | Future-positive | Future-fix rate | Relative risk vs None | Odds ratio | p |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in groups:
        group_lines.append(
            f"| {row.get('group')} | {row.get('region_count')} | {row.get('future_bug_region_count')} | {fmt_pct(row.get('future_bug_rate'))} | {fmt_float(row.get('relative_risk'))} | {fmt_float(row.get('odds_ratio'))} | {fmt_p(row.get('p_value'))} |"
        )
    conclusion = (
        "Primary H+C+S comparison is not evaluable because no region satisfies all three signals."
        if not hcs or int(float(hcs.get("region_count", 0) or 0)) == 0
        else (
            f"`H+C+S` future-fix rate: {fmt_pct(hcs.get('future_bug_rate'))}; "
            f"`None` future-fix rate: {fmt_pct(none.get('future_bug_rate'))}; "
            f"relative risk: {fmt_float(comp.get('relative_risk'))}x; p = {fmt_p(comp.get('p_value'))}."
        )
    )
    positives = int(float(hcs.get("future_bug_region_count", 0) or 0)) if hcs else 0
    if positives < 5:
        conclusion += " Positive samples are sparse, so this should be treated as effect-size evidence with limited statistical power."
    write_markdown(
        report_dir() / f"{dbms}_results.md",
        f"{dbms} Results",
        [("Primary Comparison", conclusion), ("Groups", "\n".join(group_lines))],
    )


def combined_report() -> None:
    dbms_result_report("combined")
    src = report_dir() / "combined_results.md"
    generated = report_dir() / "combined_results.md"
    if not generated.is_file():
        # dbms_result_report writes combined_results.md through this exact path.
        pass


def paper_summary() -> None:
    lines = []
    for dbms in ["mysql", "postgresql", "combined"]:
        hcs = group_row(dbms, "H+C+S")
        none = group_row(dbms, "None")
        comp = comparison_row(dbms, "H+C+S vs None")
        if not hcs or int(float(hcs.get("region_count", 0) or 0)) == 0:
            lines.append(
                f"- {dbms}: H+C+S is not evaluable in the current run because no region satisfies all three signals "
                f"(official coverage is missing, so C is false for all regions)."
            )
        else:
            lines.append(
                f"- {dbms}: H+C+S regions were {fmt_float(comp.get('relative_risk'))}x as likely as None regions to receive an independent future bug fix "
                f"({fmt_pct(hcs.get('future_bug_rate'))} vs {fmt_pct(none.get('future_bug_rate'))}, p = {fmt_p(comp.get('p_value'))})."
            )

    combined_groups = read_csv(result_dir("combined") / "group_statistics.csv")
    by_signals = {0: [], 1: [], 2: [], 3: []}
    for row in combined_groups:
        group = row.get("group", "")
        count = 0 if group == "None" else group.count("+") + 1
        by_signals[count].append(float(row.get("future_bug_rate", 0) or 0))
    monotonic = True
    prev = -1.0
    for count in [0, 1, 2, 3]:
        avg = sum(by_signals[count]) / len(by_signals[count]) if by_signals[count] else 0
        if avg < prev:
            monotonic = False
        prev = avg
    if monotonic:
        lines.append("- The mean future-fix rate increases monotonically with the number of satisfied signals.")
    else:
        lines.append("- The future-fix rate is not monotonic in the number of satisfied signals; report individual signal effects instead.")
    write_markdown(report_dir() / "paper_ready_summary.md", "Paper-Ready Summary", [("Observation 1", "\n".join(lines))])


def write_excel(data_quality_rows: list[dict[str, object]]) -> None:
    sheets = [
        ("MySQL Regions", read_csv(data_dir("mysql") / "risk_region_dataset.csv")),
        ("PostgreSQL Regions", read_csv(data_dir("postgresql") / "risk_region_dataset.csv")),
        ("MySQL Groups", read_csv(result_dir("mysql") / "group_statistics.csv")),
        ("PostgreSQL Groups", read_csv(result_dir("postgresql") / "group_statistics.csv")),
        ("Combined Groups", read_csv(result_dir("combined") / "group_statistics.csv")),
        ("Regression", read_csv(result_dir("combined") / "regression_results.csv")),
        ("Top Risk Regions", read_csv(result_dir("combined") / "top_risk_regions.csv")),
        ("Data Quality", data_quality_rows),
    ]
    write_xlsx(exp_dir() / "results" / "observation1_risk_region_validation.xlsx", sheets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    methodology_report()
    quality = data_quality_report()
    dbms_result_report("mysql")
    dbms_result_report("postgresql")
    dbms_result_report("combined")
    paper_summary()
    write_excel(quality)
    print(report_dir())


if __name__ == "__main__":
    main()
