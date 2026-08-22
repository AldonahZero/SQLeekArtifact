#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/SQLeek/experiment/RQ2/sqlaser/results/mysql827/sqlaser_prototype/formal_24h")
RUNS = [
    "r1_20260712_110033",
    "r2_20260712_110856",
    "r3_20260712_110856",
    "r4_20260712_110856",
    "r5_20260712_110857",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_sql(text: str) -> str:
    text = re.sub(r"SELECT\s+['\"]Test_ID\s+\d+['\"]\s*;", "", text, flags=re.I)
    text = re.sub(r"#MutationMark\s*", "", text, flags=re.I)
    text = re.sub(r"--[^\r\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"\s+", " ", text).strip().lower()


def query_part(text: str) -> str:
    return text.split("\nResult string:", 1)[0].removeprefix("Query:").strip()


def collect(kind: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in RUNS:
        if kind == "afl_crash":
            directory = ROOT / run / "outputs" / "outputs_0" / "crashes"
        else:
            directory = ROOT / run / "bugs" / "bug_samples"
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            if not path.is_file() or path.name.startswith("README"):
                continue
            data = path.read_bytes()
            text = data.decode("utf-8", "replace")
            normalized = normalize_sql(query_part(text) if kind == "oracle_candidate" else text)
            rows.append({
                "kind": kind,
                "run": run.split("_", 1)[0],
                "path": str(path),
                "file_name": path.name,
                "size": len(data),
                "sha256": digest(data),
                "normalized_sql_sha256": digest(normalized.encode()),
            })
    return rows


def write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    fields = ["kind", "run", "path", "file_name", "size", "sha256", "normalized_sql_sha256"]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def clusters(rows: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    result = []
    for signature, members in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        result.append({
            "signature": signature,
            "member_count": len(members),
            "runs": sorted({str(row["run"]) for row in members}),
            "representative": members[0]["path"],
            "members": [row["path"] for row in members],
        })
    return result


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = ROOT / "triage" / f"dedup_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    crash_rows = collect("afl_crash")
    oracle_rows = collect("oracle_candidate")
    all_rows = crash_rows + oracle_rows
    write_manifest(out / "candidate_manifest.tsv", all_rows)

    summary: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(ROOT),
        "runs": RUNS,
        "classification": "candidate_only_not_clean_replayed",
        "method": {
            "exact": "SHA-256 of original file bytes",
            "normalized_sql": "SHA-256 after removing Test_ID, MutationMark, SQL comments, and whitespace/case differences",
        },
    }
    for name, rows in (("afl_crashes", crash_rows), ("oracle_candidates", oracle_rows)):
        exact = clusters(rows, "sha256")
        normalized = clusters(rows, "normalized_sql_sha256")
        (out / f"{name}_exact_clusters.json").write_text(json.dumps(exact, indent=2) + "\n")
        (out / f"{name}_normalized_sql_clusters.json").write_text(json.dumps(normalized, indent=2) + "\n")
        summary[name] = {
            "files": len(rows),
            "files_by_run": dict(sorted(Counter(str(row["run"]) for row in rows).items())),
            "unique_exact_sha256": len(exact),
            "unique_normalized_sql": len(normalized),
            "exact_duplicate_files": len(rows) - len(exact),
            "normalized_duplicate_files": len(rows) - len(normalized),
            "cross_run_normalized_clusters": sum(1 for cluster in normalized if len(cluster["runs"]) > 1),
        }
    (out / "dedup_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    crash = summary["afl_crashes"]
    oracle = summary["oracle_candidates"]
    report = f"""# MySQL SQLaser candidate deduplication

This is candidate-level deduplication only. No candidate is counted as a confirmed bug without clean replay.

| Candidate source | Files | Exact SHA-256 clusters | Normalized SQL clusters | Cross-run normalized clusters |
|---|---:|---:|---:|---:|
| AFL `crashes/` | {crash['files']} | {crash['unique_exact_sha256']} | {crash['unique_normalized_sql']} | {crash['cross_run_normalized_clusters']} |
| SQLRight NoREC samples | {oracle['files']} | {oracle['unique_exact_sha256']} | {oracle['unique_normalized_sql']} | {oracle['cross_run_normalized_clusters']} |

The AFL files use `sig:00`; therefore they cannot be deduplicated by crash signal or stack at this stage. Stack-based deduplication requires isolated replay with server logs/backtraces.

Original formal outputs were not modified or deleted.
"""
    (out / "README.md").write_text(report)
    print(json.dumps({"output": str(out), "summary": summary}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
