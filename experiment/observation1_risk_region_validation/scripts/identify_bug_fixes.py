#!/usr/bin/env python3
"""Identify high-confidence bug-fix commits and aggregate unique bugs."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import random
import re
from pathlib import Path

from common import (
    canonical_dbms,
    classify_bug_type,
    component_from_path,
    data_dir,
    extract_cve_id,
    extract_issue_id,
    extract_original_hash,
    git,
    is_build_file,
    is_doc_file,
    is_generated_file,
    is_source_file,
    is_test_file,
    load_config,
    normalize_path,
    read_jsonl,
    stable_hash,
    write_csv,
)


BUG_KEYWORDS = [
    "fix",
    "bug",
    "crash",
    "segfault",
    "assert",
    "abort",
    "incorrect result",
    "wrong result",
    "use-after-free",
    "buffer overflow",
    "memory corruption",
    "memory leak",
    "null pointer",
    "deadlock",
    "race",
    "cve",
    "security",
    "reported by",
    "found by",
    "backpatch",
    "backport",
    "regression",
]
STRONG_KEYWORDS = [
    "crash",
    "segfault",
    "assert",
    "abort",
    "incorrect result",
    "wrong result",
    "use-after-free",
    "buffer overflow",
    "memory corruption",
    "null pointer",
    "deadlock",
    "race",
    "cve",
    "security",
]
EXCLUDE_HINTS = [
    "typo",
    "spelling",
    "copyright",
    "whitespace",
    "formatting",
    "reformat",
    "comment only",
    "docs only",
    "documentation",
]


def message_preclass(rec: dict[str, str]) -> dict[str, object]:
    """Cheap commit-message-only pass.

    `fix` alone is intentionally weak: large DBMS histories contain many typo,
    build, style, and cleanup commits with that word. Strong bug symptoms,
    tracker ids, CVEs, backpatch/regression wording, and reporter references
    raise the score before we pay for per-commit diff inspection.
    """
    message = (rec.get("subject", "") + "\n" + rec.get("full_message", "")).strip()
    text = message.lower()
    issue_id = extract_issue_id(message)
    cve_id = extract_cve_id(message)
    matched = [k for k in BUG_KEYWORDS if k in text]
    strong = [k for k in STRONG_KEYWORDS if k in text]
    exclude = [k for k in EXCLUDE_HINTS if k in text]
    score = 0.0
    reasons = []
    if "fix" in matched:
        score += 0.5
        reasons.append("weak_fix_word")
    if "bug" in matched:
        score += 1.5
        reasons.append("bug_word")
    if "regression" in matched:
        score += 1.5
        reasons.append("regression_word")
    if "reported by" in matched or "found by" in matched:
        score += 1.5
        reasons.append("reporter_reference")
    if "backpatch" in matched or "backport" in matched:
        score += 1.0
        reasons.append("backport_word")
    if strong:
        score += 3.0
        reasons.append("strong:" + ",".join(strong[:6]))
    if issue_id:
        score += 2.0
        reasons.append(f"issue_id:{issue_id}")
    if cve_id:
        score += 4.0
        reasons.append(f"cve:{cve_id}")
    if exclude:
        score -= 2.0
        reasons.append("exclude:" + ",".join(exclude[:5]))
    if re.search(r"(?i)\bfix(?:es|ed)?\s+(?:typo|spelling|comment|doc|documentation|format|formatting|whitespace)\b", message):
        score -= 3.0
        reasons.append("obvious_non_behavior_fix")
    return {
        "commit_hash": rec.get("commit_hash", ""),
        "commit_date": rec.get("commit_date", ""),
        "subject": rec.get("subject", ""),
        "message_score": score,
        "message_reasons": ";".join(reasons),
        "matched_keywords": ",".join(matched),
        "issue_id": issue_id,
        "cve_id": cve_id,
    }


def changed_files(repo: Path, commit: str) -> tuple[list[dict[str, str]], dict[str, int]]:
    status_text = git(repo, "diff-tree", "--no-commit-id", "--name-status", "--find-renames", "-r", commit, check=False)
    files = []
    for line in status_text.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            status, path = parts
            old_path = ""
        elif len(parts) >= 3:
            status, old_path, path = parts[0], parts[1], parts[2]
        else:
            continue
        files.append({"status": status, "path": normalize_path(path), "old_path": normalize_path(old_path)})

    churn = {"added": 0, "deleted": 0}
    numstat = git(repo, "show", "--numstat", "--format=", "--find-renames", commit, check=False)
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete = parts[0], parts[1]
        if add.isdigit():
            churn["added"] += int(add)
        if delete.isdigit():
            churn["deleted"] += int(delete)
    return files, churn


def classify_commit(
    dbms: str,
    repo: Path,
    rec: dict[str, str],
    pre: dict[str, object],
    min_message_score: float,
) -> dict[str, object] | None:
    message = (rec.get("subject", "") + "\n" + rec.get("full_message", "")).strip()
    if float(pre.get("message_score") or 0.0) < min_message_score:
        return None
    commit = rec["commit_hash"]
    files, churn = changed_files(repo, commit)
    paths = [f["path"] for f in files]
    source_files = [
        p for p in paths if is_source_file(p) and not is_test_file(p) and not is_generated_file(p)
    ]
    test_files = [p for p in paths if is_test_file(p)]
    doc_files = [p for p in paths if is_doc_file(p)]
    build_files = [p for p in paths if is_build_file(p)]
    generated_files = [p for p in paths if is_generated_file(p)]
    is_test_only = bool(paths) and bool(test_files) and not source_files
    only_non_behavior = bool(paths) and not source_files and all(
        p in set(test_files + doc_files + build_files + generated_files) for p in paths
    )

    text = message.lower()
    reasons = []
    score = 0
    matched = [k for k in BUG_KEYWORDS if k in text]
    if matched:
        score += min(3, len(matched))
        reasons.append("keyword:" + ",".join(matched[:8]))
    strong = [k for k in STRONG_KEYWORDS if k in text]
    if strong:
        score += 2
        reasons.append("strong_keyword:" + ",".join(strong[:6]))
    issue_id = str(pre.get("issue_id") or extract_issue_id(message))
    cve_id = str(pre.get("cve_id") or extract_cve_id(message))
    if issue_id:
        score += 2
        reasons.append(f"issue_id:{issue_id}")
    if cve_id:
        score += 3
        reasons.append(f"cve:{cve_id}")
    if source_files:
        score += 1
        reasons.append("source_change")
    if test_files and ("regression" in text or "test" in text):
        score += 1
        reasons.append("regression_test_added")
    if any(h in text for h in EXCLUDE_HINTS):
        score -= 1
        reasons.append("exclude_hint")
    if only_non_behavior:
        score -= 3
        reasons.append("non_behavior_only")

    is_backport = bool(re.search(r"(?i)(backpatch|backport|cherry[- ]pick|cherry picked|stable branch)", message))
    original = extract_original_hash(message)
    if is_backport:
        reasons.append("backport_or_cherry_pick")
    if original:
        reasons.append(f"original_hash:{original}")

    if is_test_only:
        confidence = "low"
    elif score >= 4 and source_files:
        confidence = "high"
    elif score >= 2 and source_files:
        confidence = "medium"
    else:
        confidence = "low"

    component_counts = collections.Counter(component_from_path(p) for p in source_files if p)
    component = component_counts.most_common(1)[0][0] if component_counts else ""
    bug_type = classify_bug_type(message)

    return {
        "dbms": dbms,
        "commit_hash": commit,
        "commit_date": rec.get("commit_date", ""),
        "parent_hash": rec.get("parent_hashes", "").split()[0] if rec.get("parent_hashes") else "",
        "subject": rec.get("subject", ""),
        "full_message": rec.get("full_message", ""),
        "issue_id": issue_id,
        "cve_id": cve_id,
        "is_bug_fix": confidence == "high",
        "confidence": confidence,
        "confidence_score": score,
        "confidence_reasons": f"message_score={pre.get('message_score')};{pre.get('message_reasons')};" + ";".join(reasons),
        "is_backport": is_backport,
        "original_fix_hash": original,
        "is_test_only": is_test_only,
        "regression_test_added": bool(test_files),
        "bug_type": bug_type,
        "component": component,
        "changed_source_files": ";".join(sorted(set(source_files))),
        "changed_test_files": ";".join(sorted(set(test_files))),
        "changed_doc_files": ";".join(sorted(set(doc_files))),
        "changed_build_files": ";".join(sorted(set(build_files))),
        "changed_generated_files": ";".join(sorted(set(generated_files))),
        "churn_added": churn["added"],
        "churn_deleted": churn["deleted"],
        "churn_total": churn["added"] + churn["deleted"],
        "changed_file_count": len(paths),
    }


def normalize_subject(subject: str) -> str:
    text = subject.lower()
    text = re.sub(r"\b(backpatch|backport|cherry[- ]pick(ed)?|to|from|branch|stable)\b", " ", text)
    text = re.sub(r"\b(rel_)?\d+(_stable)?\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())[:120]


def unique_bug_id(dbms: str, row: dict[str, object]) -> str:
    cve = str(row.get("cve_id") or "")
    issue = str(row.get("issue_id") or "")
    original = str(row.get("original_fix_hash") or "")
    if cve:
        return f"{dbms}:cve:{cve.split(';')[0]}"
    if issue:
        return f"{dbms}:issue:{issue.split(';')[0]}"
    if original:
        return f"{dbms}:orig:{original[:12]}"
    subject = normalize_subject(str(row.get("subject") or ""))
    return f"{dbms}:subject:{stable_hash(subject, 16)}"


def aggregate_unique_bugs(dbms: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    for row in rows:
        if row.get("confidence") != "high":
            continue
        grouped[unique_bug_id(dbms, row)].append(row)
    out = []
    for bug_id, items in sorted(grouped.items(), key=lambda kv: min(str(x["commit_date"]) for x in kv[1])):
        dates = sorted(str(x["commit_date"]) for x in items)
        commits = [str(x["commit_hash"]) for x in items]
        first = min(items, key=lambda r: str(r["commit_date"]))
        all_source = sorted(
            set(
                p
                for item in items
                for p in str(item.get("changed_source_files") or "").split(";")
                if p
            )
        )
        types = collections.Counter(str(x.get("bug_type") or "") for x in items)
        components = collections.Counter(str(x.get("component") or "") for x in items if x.get("component"))
        out.append(
            {
                "dbms": dbms,
                "unique_bug_id": bug_id,
                "first_commit_hash": first["commit_hash"],
                "first_commit_date": dates[0],
                "last_commit_date": dates[-1],
                "commit_hashes": ";".join(commits),
                "commit_count": len(items),
                "subject": first.get("subject", ""),
                "issue_id": first.get("issue_id", ""),
                "cve_id": first.get("cve_id", ""),
                "is_backport_group": any(bool(x.get("is_backport")) for x in items),
                "original_fix_hash": first.get("original_fix_hash", ""),
                "bug_type": types.most_common(1)[0][0] if types else "",
                "component": components.most_common(1)[0][0] if components else "",
                "changed_source_files": ";".join(all_source),
            }
        )
    return out


FIELDS = [
    "dbms",
    "commit_hash",
    "commit_date",
    "parent_hash",
    "subject",
    "full_message",
    "issue_id",
    "cve_id",
    "is_bug_fix",
    "confidence",
    "confidence_score",
    "confidence_reasons",
    "is_backport",
    "original_fix_hash",
    "is_test_only",
    "regression_test_added",
    "bug_type",
    "component",
    "changed_source_files",
    "changed_test_files",
    "changed_doc_files",
    "changed_build_files",
    "changed_generated_files",
    "churn_added",
    "churn_deleted",
    "churn_total",
    "changed_file_count",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument(
        "--message-threshold",
        type=float,
        default=2.0,
        help="Only commits with this cheap message score receive diff inspection.",
    )
    parser.add_argument(
        "--message-only",
        action="store_true",
        help="Only write message-level candidates and skip diff inspection.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dbms = canonical_dbms(args.dbms)
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    out = data_dir(dbms) / "bug_fix_commits.csv"
    unique_out = data_dir(dbms) / "unique_bugs.csv"
    sample_out = data_dir(dbms) / "bug_fix_candidate_audit_sample.csv"
    message_out = data_dir(dbms) / "bug_fix_message_candidates.csv"
    if out.is_file() and unique_out.is_file() and not args.force:
        print(out)
        return

    commits = read_jsonl(data_dir(dbms) / "git_commits_raw.jsonl")
    pre_rows = [message_preclass(rec) for rec in commits]
    message_candidates = [r for r in pre_rows if float(r.get("message_score") or 0.0) >= args.message_threshold]
    write_csv(message_out, message_candidates)
    if args.message_only:
        print(f"{message_out} message_candidates={len(message_candidates)} total_commits={len(commits)}")
        return
    pre_by_hash = {str(r["commit_hash"]): r for r in pre_rows}
    commit_by_hash = {r["commit_hash"]: r for r in commits}

    rows = []
    for idx, pre in enumerate(message_candidates, start=1):
        rec = commit_by_hash[str(pre["commit_hash"])]
        row = classify_commit(dbms, repo, rec, pre, args.message_threshold)
        if row is not None:
            rows.append(row)
        if idx % 1000 == 0:
            print(
                f"[{dbms}] diff-inspected {idx}/{len(message_candidates)} message candidates, retained={len(rows)}",
                flush=True,
            )

    write_csv(out, rows, FIELDS)
    unique_rows = aggregate_unique_bugs(dbms, rows)
    write_csv(unique_out, unique_rows)

    rng = random.Random(20260716)
    sample = rows if len(rows) <= args.sample_size else rng.sample(rows, args.sample_size)
    write_csv(sample_out, sample, FIELDS)
    print(
        f"{out} message_candidates={len(message_candidates)} diff_candidates={len(rows)} "
        f"high_conf={sum(1 for r in rows if r['confidence']=='high')} unique={len(unique_rows)}"
    )


if __name__ == "__main__":
    main()
