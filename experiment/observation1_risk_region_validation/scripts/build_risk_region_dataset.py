#!/usr/bin/env python3
"""Build windowed risk-region datasets for Observation 1."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path

from common import (
    canonical_dbms,
    data_dir,
    date_only,
    git,
    int_or_zero,
    load_config,
    median,
    normalize_path,
    read_csv,
    safe_div,
    truthy,
    write_csv,
)


def parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return date_only(value)
    except Exception:
        try:
            return dt.date.fromisoformat(value[:10])
        except Exception:
            return None


def windows_from_config(cfg: dict[str, object]) -> list[dict[str, object]]:
    windows_cfg = cfg.get("windows") or {}
    out = []
    if isinstance(windows_cfg, dict) and windows_cfg.get("main"):
        main = dict(windows_cfg["main"])
        main["primary_window"] = True
        out.append(main)
    for item in (windows_cfg.get("rolling") if isinstance(windows_cfg, dict) else []) or []:
        row = dict(item)
        row["primary_window"] = False
        out.append(row)
    if not out:
        raise RuntimeError("config does not contain time windows; run extract_git_history.py first")
    return out


def coverage_by_region(dbms: str) -> dict[str, dict[str, str]]:
    return {r["region_id"]: r for r in read_csv(data_dir(dbms) / "official_coverage.csv")}


def reachability_by_region(dbms: str) -> dict[str, dict[str, str]]:
    return {r["region_id"]: r for r in read_csv(data_dir(dbms) / "sql_reachability.csv")}


def mappings_by_region(dbms: str) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in read_csv(data_dir(dbms) / "bug_region_mapping.csv"):
        if row.get("region_id"):
            out[row["region_id"]].append(row)
    return out


def commit_lookup(dbms: str) -> dict[str, dict[str, str]]:
    return {r["commit_hash"]: r for r in read_csv(data_dir(dbms) / "bug_fix_commits.csv")}


def unique_lookup(dbms: str) -> dict[str, dict[str, str]]:
    return {r["unique_bug_id"]: r for r in read_csv(data_dir(dbms) / "unique_bugs.csv")}


def file_history_stats(repo: Path, file_path: str, hist_end: str, cache: Path) -> dict[str, object]:
    cache.mkdir(parents=True, exist_ok=True)
    key = normalize_path(file_path).replace("/", "__")
    out = cache / f"{key}_{hist_end}.json"
    if out.is_file():
        return json.loads(out.read_text(encoding="utf-8"))
    path = normalize_path(file_path)
    log_text = git(repo, "log", "--follow", "--date=iso-strict", f"--until={hist_end} 23:59:59", "--format=%H%x1f%aI%x1f%ae", "--", path, check=False)
    commits = []
    authors = set()
    dates = []
    for line in log_text.splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 3:
            commits.append(parts[0])
            authors.add(parts[2])
            d = parse_date(parts[1])
            if d:
                dates.append(d)
    numstat = git(repo, "log", "--follow", f"--until={hist_end} 23:59:59", "--numstat", "--format=COMMIT", "--", path, check=False)
    added = deleted = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            if parts[0].isdigit():
                added += int(parts[0])
            if parts[1].isdigit():
                deleted += int(parts[1])
    end_date = dt.date.fromisoformat(hist_end)
    first_date = min(dates) if dates else end_date
    stats = {
        "total_commit_count": len(set(commits)),
        "file_commit_count": len(set(commits)),
        "unique_author_count": len(authors),
        "code_churn_added": added,
        "code_churn_deleted": deleted,
        "code_churn_total": added + deleted,
        "region_age_days": max(0, (end_date - first_date).days),
        "control_history_method": "file_level_git_history_proxy",
    }
    out.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stats


def bug_type_counts(bug_ids: set[str], unique: dict[str, dict[str, str]]) -> tuple[int, int, int]:
    security = crash = incorrect = 0
    for bug_id in bug_ids:
        btype = (unique.get(bug_id, {}).get("bug_type") or "").lower()
        if btype == "security":
            security += 1
        if btype == "crash":
            crash += 1
        if btype == "incorrect_result":
            incorrect += 1
    return security, crash, incorrect


def first_date_for_bug(bug_id: str, unique: dict[str, dict[str, str]]) -> dt.date | None:
    return parse_date(unique.get(bug_id, {}).get("first_commit_date", ""))


def branch_cov_value(cov: dict[str, str]) -> float | None:
    val = cov.get("official_branch_coverage")
    if val == "" or val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def line_cov_value(cov: dict[str, str]) -> float | None:
    val = cov.get("official_line_coverage")
    if val == "" or val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def build_dataset(dbms: str) -> list[dict[str, object]]:
    dbms = canonical_dbms(dbms)
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    regions = read_csv(data_dir(dbms) / "risk_regions.csv")
    coverage = coverage_by_region(dbms)
    reachability = reachability_by_region(dbms)
    mappings = mappings_by_region(dbms)
    commits = commit_lookup(dbms)
    unique = unique_lookup(dbms)
    windows = windows_from_config(cfg)

    branch_values = [v for v in (branch_cov_value(coverage.get(r["region_id"], {})) for r in regions) if v is not None]
    line_values = [v for v in (line_cov_value(coverage.get(r["region_id"], {})) for r in regions) if v is not None]
    branch_median = median(branch_values) if branch_values else math.nan
    line_median = median(line_values) if line_values else math.nan

    rows = []
    file_cache = data_dir(dbms) / "file_history_cache"
    for win in windows:
        hist_start = dt.date.fromisoformat(str(win["historical_start"]))
        hist_end = dt.date.fromisoformat(str(win["historical_end"]))
        val_start = dt.date.fromisoformat(str(win["validation_start"]))
        val_end = dt.date.fromisoformat(str(win["validation_end"]))
        for idx, region in enumerate(regions, start=1):
            region_maps = [m for m in mappings.get(region["region_id"], []) if m.get("mapping_confidence") == "high"]
            hist_bug_ids: set[str] = set()
            hist_commits = []
            future_bug_ids: set[str] = set()
            future_commits = []
            last_hist_fix: dt.date | None = None
            first_future: dt.date | None = None
            for mapping in region_maps:
                commit_date = parse_date(mapping.get("commit_date", ""))
                bug_id = mapping.get("unique_bug_id", "")
                commit_hash = mapping.get("commit_hash", "")
                if not commit_date or not bug_id:
                    continue
                bug_first = first_date_for_bug(bug_id, unique) or commit_date
                if hist_start <= commit_date <= hist_end:
                    hist_bug_ids.add(bug_id)
                    hist_commits.append(commit_hash)
                    if last_hist_fix is None or commit_date > last_hist_fix:
                        last_hist_fix = commit_date
                elif val_start <= commit_date <= val_end:
                    # Exclude backports/supplementary commits for bugs whose first fix belongs to history.
                    if bug_id in hist_bug_ids or bug_first <= hist_end:
                        continue
                    future_bug_ids.add(bug_id)
                    future_commits.append(commit_hash)
                    if first_future is None or commit_date < first_future:
                        first_future = commit_date

            h_sec, h_crash, h_incorrect = bug_type_counts(hist_bug_ids, unique)
            f_sec, f_crash, _ = bug_type_counts(future_bug_ids, unique)
            days_since_last = (hist_end - last_hist_fix).days if last_hist_fix else ""
            days_to_first = (first_future - val_start).days if first_future else ""
            cov = coverage.get(region["region_id"], {})
            reach = reachability.get(region["region_id"], {})
            branch_cov = branch_cov_value(cov)
            line_cov = line_cov_value(cov)
            coverage_missing = branch_cov is None
            coverage_gap = False
            coverage_gap_basis = "missing_branch_coverage"
            if branch_cov is not None and not math.isnan(branch_median):
                coverage_gap = branch_cov < branch_median
                coverage_gap_basis = "official_branch_coverage_below_dbms_median"
            elif line_cov is not None and not math.isnan(line_median):
                # Sensitivity fallback only: main C remains false if branch coverage is missing.
                coverage_gap_basis = "line_coverage_available_but_branch_missing"
            historical_count = len(hist_bug_ids)
            h_signal = historical_count >= int((cfg.get("analysis") or {}).get("historical_repair_threshold", 2))
            s_signal = truthy(reach.get("sql_reachable"))
            controls = file_history_stats(repo, region["file_path"], str(win["historical_end"]), file_cache)
            rows.append(
                {
                    **region,
                    "window_id": win["window_id"],
                    "primary_window": bool(win.get("primary_window")),
                    "historical_start": win["historical_start"],
                    "historical_end": win["historical_end"],
                    "validation_start": win["validation_start"],
                    "validation_end": win["validation_end"],
                    "historical_unique_bug_count": historical_count,
                    "historical_bug_fix_commit_count": len(set(hist_commits)),
                    "historical_security_bug_count": h_sec,
                    "historical_crash_bug_count": h_crash,
                    "historical_incorrect_result_bug_count": h_incorrect,
                    "days_since_last_historical_fix": days_since_last,
                    "historical_repair_signal": h_signal,
                    "official_test_covered": cov.get("official_test_covered", ""),
                    "official_line_coverage": "" if line_cov is None else line_cov,
                    "official_branch_coverage": "" if branch_cov is None else branch_cov,
                    "covered_lines": cov.get("covered_lines", ""),
                    "total_lines": cov.get("total_lines", ""),
                    "covered_branches": cov.get("covered_branches", ""),
                    "total_branches": cov.get("total_branches", ""),
                    "coverage_source": cov.get("coverage_source", "missing_official_test_coverage"),
                    "coverage_commit": cov.get("coverage_commit", ""),
                    "coverage_missing": coverage_missing,
                    "coverage_gap_signal": coverage_gap,
                    "coverage_gap_basis": coverage_gap_basis,
                    "sql_reachable": s_signal,
                    "sql_entry_count": reach.get("sql_entry_count", ""),
                    "nearest_sql_entry": reach.get("nearest_sql_entry", ""),
                    "shortest_call_distance": reach.get("shortest_call_distance", ""),
                    "callchain_count": reach.get("callchain_count", ""),
                    "reachability_method": reach.get("reachability_method", "missing"),
                    "reachability_commit": reach.get("reachability_commit", ""),
                    "future_unique_bug_count": len(future_bug_ids),
                    "future_bug_fix_commit_count": len(set(future_commits)),
                    "future_bug_fixed": bool(future_bug_ids),
                    "future_security_bug_count": f_sec,
                    "future_crash_bug_count": f_crash,
                    "first_future_bug_date": first_future.isoformat() if first_future else "",
                    "days_to_first_future_bug": days_to_first,
                    "future_unique_bug_ids": ";".join(sorted(future_bug_ids)),
                    "historical_unique_bug_ids": ";".join(sorted(hist_bug_ids)),
                    **controls,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dbms = canonical_dbms(args.dbms)
    out = data_dir(dbms) / "risk_region_dataset.csv"
    if out.is_file() and not args.force:
        print(out)
        return
    rows = build_dataset(dbms)
    write_csv(out, rows)
    print(f"{out} rows={len(rows)}")


if __name__ == "__main__":
    main()
