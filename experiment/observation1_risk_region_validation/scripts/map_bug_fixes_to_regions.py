#!/usr/bin/env python3
"""Map bug-fix commits to Stage 1 risk regions."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import canonical_dbms, data_dir, git, is_source_file, load_config, normalize_path, read_csv, run, write_csv


HUNK_RE = re.compile(r"@@ -(?P<old>\d+)(?:,(?P<oldn>\d+))? \+(?P<new>\d+)(?:,(?P<newn>\d+))? @@(?P<context>.*)")
HASH_RE = re.compile(r"\b[0-9a-f]{40}\b")


def intv(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except Exception:
        return default


def overlap(a1: int, a2: int, b1: int, b2: int) -> int:
    lo = max(a1, b1)
    hi = min(a2, b2)
    return max(0, hi - lo + 1)


def hunk_ranges(repo: Path, commit: str) -> list[dict[str, object]]:
    text = git(repo, "show", "--unified=0", "--format=", "--find-renames", commit, check=False)
    hunks = []
    current = ""
    old = ""
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = ""
            old = ""
        elif line.startswith("rename from "):
            old = normalize_path(line[len("rename from ") :])
        elif line.startswith("rename to "):
            current = normalize_path(line[len("rename to ") :])
        elif line.startswith("--- "):
            old = normalize_path(line[6:] if line.startswith("--- a/") else line[4:])
        elif line.startswith("+++ "):
            current = normalize_path(line[6:] if line.startswith("+++ b/") else line[4:])
        elif line.startswith("@@ "):
            m = HUNK_RE.match(line)
            if not m or not current or current == "/dev/null" or not is_source_file(current):
                continue
            new_start = int(m.group("new"))
            new_count = int(m.group("newn") or "1")
            old_start = int(m.group("old"))
            old_count = int(m.group("oldn") or "1")
            if new_count == 0:
                changed_start = new_start
                changed_end = new_start
            else:
                changed_start = new_start
                changed_end = new_start + new_count - 1
            hunks.append(
                {
                    "file_path": current,
                    "old_file_path": old,
                    "changed_start_line": changed_start,
                    "changed_end_line": changed_end,
                    "old_start_line": old_start,
                    "old_end_line": old_start + max(1, old_count) - 1,
                    "hunk_context": m.group("context").strip(),
                }
            )
    return hunks


def suffix_same(a: str, b: str) -> bool:
    a = normalize_path(a)
    b = normalize_path(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def line_history_commits(
    repo: Path,
    dbms: str,
    region: dict[str, str],
    cache_dir: Path,
    cache_only: bool = False,
) -> set[str] | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{region['region_id']}.txt"
    if cache.is_file():
        return set(
            x.strip()
            for x in cache.read_text(encoding="utf-8", errors="replace").splitlines()
            if x.strip() and not x.startswith("#")
        )
    if cache_only:
        return None
    path = normalize_path(region["file_path"])
    start = intv(region["start_line"])
    end = intv(region["end_line"], start)
    commits: set[str] = set()
    if start <= 0 or end <= 0:
        cache.write_text("", encoding="utf-8")
        return commits
    proc = run(
        ["git", "-C", str(repo), "log", "--no-merges", "--format=%H", f"-L{start},{end}:{path}"],
        check=False,
    )
    if proc.returncode == 0:
        commits = set(HASH_RE.findall(proc.stdout))
    cache.write_text("\n".join(sorted(commits)) + ("\n" if commits else ""), encoding="utf-8")
    return commits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-line-history", action="store_true")
    parser.add_argument(
        "--line-history-cache-only",
        action="store_true",
        help="Use existing git-log-L cache only; skip uncached regions instead of invoking more history mining.",
    )
    parser.add_argument(
        "--promote-current-overlap",
        action="store_true",
        help="Treat current-coordinate diff overlap as high-confidence fast statistical approximation.",
    )
    args = parser.parse_args()

    dbms = canonical_dbms(args.dbms)
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    out = data_dir(dbms) / "bug_region_mapping.csv"
    if out.is_file() and not args.force:
        print(out)
        return

    regions = read_csv(data_dir(dbms) / "risk_regions.csv")
    commits = [r for r in read_csv(data_dir(dbms) / "bug_fix_commits.csv") if r.get("confidence") == "high"]
    unique = read_csv(data_dir(dbms) / "unique_bugs.csv")
    commit_to_bug = {}
    for bug in unique:
        for h in (bug.get("commit_hashes") or "").split(";"):
            if h:
                commit_to_bug[h] = bug["unique_bug_id"]
    commit_dates = {r["commit_hash"]: r.get("commit_date", "") for r in commits}

    hunk_cache: dict[str, list[dict[str, object]]] = {}

    def hunks_for(commit: str) -> list[dict[str, object]]:
        if commit not in hunk_cache:
            hunk_cache[commit] = hunk_ranges(repo, commit)
        return hunk_cache[commit]

    rows: list[dict[str, object]] = []
    mapped_pairs: set[tuple[str, str]] = set()

    if not args.skip_line_history:
        cache_dir = data_dir(dbms) / "line_history_cache"
        high_commits = set(commit_to_bug)
        for idx, region in enumerate(regions, start=1):
            if idx % 100 == 0:
                print(f"[{dbms}] line-history regions {idx}/{len(regions)}", flush=True)
            history = line_history_commits(repo, dbms, region, cache_dir, args.line_history_cache_only)
            if history is None:
                rows.append(
                    {
                        "dbms": dbms,
                        "unique_bug_id": "",
                        "commit_hash": "",
                        "commit_date": "",
                        "region_id": region["region_id"],
                        "file_path": region["file_path"],
                        "changed_start_line": "",
                        "changed_end_line": "",
                        "overlap_lines": 0,
                        "mapping_method": "line_history_skipped",
                        "mapping_confidence": "low",
                        "mapping_evidence": "line_history_cache_missing_fast_mode",
                    }
                )
                continue
            touched = history & high_commits
            for commit in sorted(touched):
                hunks = [h for h in hunks_for(commit) if suffix_same(str(h["file_path"]), region["file_path"])]
                changed_start = intv(region["start_line"])
                changed_end = intv(region["end_line"], changed_start)
                if hunks:
                    changed_start = intv(hunks[0]["changed_start_line"])
                    changed_end = intv(hunks[0]["changed_end_line"], changed_start)
                key = (commit, region["region_id"])
                mapped_pairs.add(key)
                rows.append(
                    {
                        "dbms": dbms,
                        "unique_bug_id": commit_to_bug.get(commit, ""),
                        "commit_hash": commit,
                        "commit_date": commit_dates.get(commit, ""),
                        "region_id": region["region_id"],
                        "file_path": region["file_path"],
                        "changed_start_line": changed_start,
                        "changed_end_line": changed_end,
                        "overlap_lines": intv(region.get("region_loc"), 1),
                        "mapping_method": "exact_overlap",
                        "mapping_confidence": "high",
                        "mapping_evidence": "git_log_L_region_line_history",
                    }
                )

    region_by_file: dict[str, list[dict[str, str]]] = {}
    for region in regions:
        region_by_file.setdefault(normalize_path(region["file_path"]), []).append(region)

    for rec in commits:
        commit = rec["commit_hash"]
        for hunk in hunks_for(commit):
            hfile = str(hunk["file_path"])
            candidates = []
            for fpath, items in region_by_file.items():
                if suffix_same(hfile, fpath):
                    candidates.extend(items)
            for region in candidates:
                key = (commit, region["region_id"])
                if key in mapped_pairs:
                    continue
                ov = overlap(
                    intv(hunk["changed_start_line"]),
                    intv(hunk["changed_end_line"]),
                    intv(region["start_line"]),
                    intv(region["end_line"], intv(region["start_line"])),
                )
                if ov > 0:
                    fast_conf = "high" if args.promote_current_overlap else "medium"
                    fast_method = "current_coordinate_overlap" if args.promote_current_overlap else "partial_overlap"
                    rows.append(
                        {
                            "dbms": dbms,
                            "unique_bug_id": commit_to_bug.get(commit, ""),
                            "commit_hash": commit,
                            "commit_date": rec.get("commit_date", ""),
                            "region_id": region["region_id"],
                            "file_path": region["file_path"],
                            "changed_start_line": hunk["changed_start_line"],
                            "changed_end_line": hunk["changed_end_line"],
                            "overlap_lines": ov,
                            "mapping_method": fast_method,
                            "mapping_confidence": fast_conf,
                            "mapping_evidence": "current_line_overlap_fast_statistical_approximation"
                            if args.promote_current_overlap
                            else "current_line_overlap_sensitivity_only",
                        }
                    )
                    mapped_pairs.add(key)
                elif str(region.get("enclosing_function") or "") and str(region.get("enclosing_function")) in str(hunk.get("hunk_context") or ""):
                    rows.append(
                        {
                            "dbms": dbms,
                            "unique_bug_id": commit_to_bug.get(commit, ""),
                            "commit_hash": commit,
                            "commit_date": rec.get("commit_date", ""),
                            "region_id": region["region_id"],
                            "file_path": region["file_path"],
                            "changed_start_line": hunk["changed_start_line"],
                            "changed_end_line": hunk["changed_end_line"],
                            "overlap_lines": 0,
                            "mapping_method": "function_context",
                            "mapping_confidence": "low",
                            "mapping_evidence": "diff_hunk_context_function_sensitivity_only",
                        }
                    )
                    mapped_pairs.add(key)

    mapped_commits = {commit for commit, _ in mapped_pairs}
    for rec in commits:
        commit = rec["commit_hash"]
        if commit in mapped_commits:
            continue
        rows.append(
            {
                "dbms": dbms,
                "unique_bug_id": commit_to_bug.get(commit, ""),
                "commit_hash": commit,
                "commit_date": rec.get("commit_date", ""),
                "region_id": "",
                "file_path": "",
                "changed_start_line": "",
                "changed_end_line": "",
                "overlap_lines": 0,
                "mapping_method": "unmapped",
                "mapping_confidence": "low",
                "mapping_evidence": "no_direct_region_match",
            }
        )

    fields = [
        "dbms",
        "unique_bug_id",
        "commit_hash",
        "commit_date",
        "region_id",
        "file_path",
        "changed_start_line",
        "changed_end_line",
        "overlap_lines",
        "mapping_method",
        "mapping_confidence",
        "mapping_evidence",
    ]
    write_csv(out, rows, fields)
    print(f"{out} mappings={len(rows)} high={sum(1 for r in rows if r['mapping_confidence']=='high')}")


if __name__ == "__main__":
    main()
