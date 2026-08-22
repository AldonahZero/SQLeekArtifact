#!/usr/bin/env python3
"""Extract DBMS Git history and derive temporal windows."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from common import (
    append_jsonl,
    canonical_dbms,
    data_dir,
    date_to_iso,
    dump_yaml,
    ensure_tree,
    exp_dir,
    git,
    load_config,
    parse_iso_date,
    read_jsonl,
)


def parse_git_records(text: str) -> list[dict[str, str]]:
    records = []
    for raw in text.split("\x1e"):
        raw = raw.strip("\n")
        if not raw:
            continue
        parts = raw.split("\x1f", 6)
        if len(parts) != 7:
            continue
        h, parents, date, author, email, subject, message = parts
        records.append(
            {
                "commit_hash": h.strip(),
                "parent_hashes": parents.strip(),
                "commit_date": date.strip(),
                "author_name": author.strip(),
                "author_email": email.strip(),
                "subject": subject.strip(),
                "full_message": message.strip(),
            }
        )
    return records


def compute_windows(commits: list[dict[str, str]], today: dt.date) -> dict[str, object]:
    dates = sorted(parse_iso_date(c["commit_date"]).date() for c in commits if c.get("commit_date"))
    if not dates:
        raise RuntimeError("no commit dates found")
    first = dates[0]
    latest = dates[-1]
    latest_complete_year = min(today.year - 1, latest.year if latest.year < today.year else today.year - 1)
    if latest_complete_year < first.year + 1:
        latest_complete_year = latest.year
    validation_start = dt.date(latest_complete_year - 1, 1, 1)
    validation_end = dt.date(latest_complete_year, 12, 31)
    historical_end = validation_start - dt.timedelta(days=1)
    main = {
        "window_id": "main",
        "historical_start": first.isoformat(),
        "historical_end": historical_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
    }

    rolling = []
    start_hist_end_year = max(first.year, latest_complete_year - 5)
    for hist_end_year in range(start_hist_end_year, latest_complete_year - 1):
        val_start_year = hist_end_year + 1
        val_end_year = hist_end_year + 2
        if val_end_year > latest_complete_year:
            continue
        rolling.append(
            {
                "window_id": f"hist_to_{hist_end_year}_val_{val_start_year}_{val_end_year}",
                "historical_start": first.isoformat(),
                "historical_end": f"{hist_end_year}-12-31",
                "validation_start": f"{val_start_year}-01-01",
                "validation_end": f"{val_end_year}-12-31",
            }
        )
    # Guarantee at least three windows when repository history allows it.
    if len(rolling) < 3:
        earliest = max(first.year, latest_complete_year - 4)
        rolling = []
        for hist_end_year in range(earliest, latest_complete_year - 1):
            val_start_year = hist_end_year + 1
            val_end_year = hist_end_year + 2
            if val_end_year <= latest_complete_year:
                rolling.append(
                    {
                        "window_id": f"hist_to_{hist_end_year}_val_{val_start_year}_{val_end_year}",
                        "historical_start": first.isoformat(),
                        "historical_end": f"{hist_end_year}-12-31",
                        "validation_start": f"{val_start_year}-01-01",
                        "validation_end": f"{val_end_year}-12-31",
                    }
                )
    return {
        "repo_first_commit_date": first.isoformat(),
        "repo_latest_commit_date": latest.isoformat(),
        "latest_complete_year": latest_complete_year,
        "main": main,
        "rolling": rolling,
    }


def update_config(
    dbms: str,
    cfg: dict[str, object],
    commits: list[dict[str, str]],
    windows: dict[str, object],
    history_scope: str,
) -> None:
    repo = Path(str(cfg["source_repo"]))
    cfg["source_commit"] = git(repo, "rev-parse", "HEAD").strip()
    cfg["history"] = {
        "repo_first_commit_date": windows["repo_first_commit_date"],
        "repo_latest_commit_date": windows["repo_latest_commit_date"],
        "latest_complete_year": windows["latest_complete_year"],
        "commit_count_no_merges": len(commits),
        "history_scope": history_scope,
    }
    cfg["windows"] = {"main": windows["main"], "rolling": windows["rolling"]}
    dump_yaml(exp_dir() / "configs" / f"{canonical_dbms(dbms)}.yaml", cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--all-branches", action="store_true", help="Use --all instead of the checked-out branch.")
    parser.add_argument("--force", action="store_true", help="Re-extract even if cache exists.")
    parser.add_argument("--current-date", default=os.environ.get("SQLEEK_OBS1_CURRENT_DATE", ""))
    args = parser.parse_args()

    ensure_tree()
    dbms = canonical_dbms(args.dbms)
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    out_path = data_dir(dbms) / "git_commits_raw.jsonl"
    summary_path = data_dir(dbms) / "git_history_summary.json"

    if out_path.is_file() and not args.force:
        commits = read_jsonl(out_path)
    else:
        if out_path.exists():
            out_path.unlink()
        fmt = "%x1e%H%x1f%P%x1f%aI%x1f%an%x1f%ae%x1f%s%x1f%B"
        rev_args = ["--all"] if args.all_branches else ["HEAD"]
        text = git(repo, "log", *rev_args, "--no-merges", "--date=iso-strict", f"--format={fmt}")
        commits = parse_git_records(text)
        append_jsonl(out_path, commits)

    today = dt.date.fromisoformat(args.current_date) if args.current_date else dt.date.today()
    windows = compute_windows(commits, today)
    history_scope = "all_branches" if args.all_branches else "current_branch_head"
    update_config(dbms, cfg, commits, windows, history_scope)
    summary = {
        "dbms": dbms,
        "source_repo": str(repo),
        "source_commit": git(repo, "rev-parse", "HEAD").strip(),
        "history_scope": history_scope,
        "commit_count_no_merges": len(commits),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "windows": windows,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
