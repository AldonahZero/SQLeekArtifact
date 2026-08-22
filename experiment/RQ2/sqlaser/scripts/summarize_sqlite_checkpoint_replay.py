#!/usr/bin/env python3
"""Summarize SQLite unified LLVM checkpoint replay coverage."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def norm(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def regions(path: Path, amalgamation: Path) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open())) if path.exists() else []
    offsets = {}
    if amalgamation.exists():
        marker = re.compile(r"/\*+\s*Begin file\s+([^*\s]+)\s*\*+/")
        for line_no, line in enumerate(amalgamation.read_text(errors="replace").splitlines(), 1):
            match = marker.search(line)
            if match:
                offsets[Path(match.group(1)).name] = line_no
    output = []
    for row in rows:
        if row.get("dbms") != "sqlite":
            continue
        output.append(row)
        source = Path(norm(row.get("source_path", row.get("file", "")))).name
        if source in offsets and row.get("file") != "sqlite3.c":
            clone = dict(row)
            clone["file"] = "sqlite3.c"; clone["source_path"] = "sqlite3.c"
            clone["start_line"] = str(offsets[source] + int(row["start_line"]))
            clone["end_line"] = str(offsets[source] + int(row["end_line"]))
            output.append(clone)
    return output


def parse_cov(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(errors="replace")).get("data") or [{}]
    branches = []; lines = []
    for file_obj in data[0].get("files") or []:
        filename = str(file_obj.get("filename") or file_obj.get("name") or "")
        for item in file_obj.get("branches") or []:
            if len(item) < 5:
                continue
            branches.append({"file": filename, "start": int(item[0]), "end": int(item[2]) or int(item[0]), "true": int(item[4]), "false": int(item[5]) if len(item) > 5 else None})
        for item in file_obj.get("segments") or []:
            if len(item) >= 3 and (len(item) < 4 or bool(item[3])):
                lines.append({"file": filename, "line": int(item[0]), "count": int(item[2])})
    return branches, lines


def matches(file_name: str, row: dict[str, str]) -> bool:
    candidate = norm(file_name); source = norm(row.get("source_path", ""))
    return candidate == source or candidate.endswith("/" + source) or Path(candidate).name == row.get("file")


def in_target(file_name: str, start: int, end: int, target: list[dict[str, str]]) -> bool:
    return any(matches(file_name, row) and int(row["start_line"]) <= end and start <= int(row["end_line"]) for row in target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    target_path = args.replay_dir / "target_regions.csv"
    if not target_path.exists():
        import subprocess
        subprocess.run(["python3", "/root/SQLeek/experiment/RQ2/replay/build_target_regions.py", "--dbms", "sqlite", "--out", str(target_path)], check=True)
    target = regions(target_path, Path("/root/SQLeek/sources/sqlite/sqlite3.c"))
    with (args.replay_dir / "replay_index.tsv").open() as fp:
        index = list(csv.DictReader(fp, delimiter="\t"))
    result = []
    for row in index:
        cov = Path(row["cov_json"])
        if row["status"] != "complete" or not cov.exists():
            continue
        branches, lines = parse_cov(cov)
        branch_total = len(branches) * 2
        branch_hit = sum(int(b["true"] > 0) + int(b["false"] is not None and b["false"] > 0) for b in branches)
        unique_lines = {(norm(item["file"]), item["line"]) for item in lines}
        line_hits = {(norm(item["file"]), item["line"]) for item in lines if item["count"] > 0}
        risk = [b for b in branches if in_target(b["file"], b["start"], b["end"], target)]
        risk_total = len(risk) * 2
        risk_hit = sum(int(b["true"] > 0) + int(b["false"] is not None and b["false"] > 0) for b in risk)
        hit_targets = set()
        all_targets = {row.get("region_id", "") for row in target}
        for target_row in target:
            if any(matches(line["file"], target_row) and int(target_row["start_line"]) <= line["line"] <= int(target_row["end_line"]) and line["count"] > 0 for line in lines):
                hit_targets.add(target_row.get("region_id", ""))
        result.append({
            "run_id": row["run_id"], "tool": row["tool"], "dbms": row["dbms"], "repeat_id": row["repeat_id"], "checkpoint_min": int(row["checkpoint_min"]),
            "risk_branches_total": risk_total, "risk_branches_hit": risk_hit, "target_region_branch_coverage": risk_hit / risk_total if risk_total else 0.0,
            "target_total": len(all_targets), "target_hit": len(hit_targets), "target_hit_rate": len(hit_targets) / len(all_targets) if all_targets else 0.0,
            "global_branches_total": branch_total, "global_branches_hit": branch_hit, "global_branch_coverage": branch_hit / branch_total if branch_total else 0.0,
            "global_lines_total": len(unique_lines), "global_lines_hit": len(line_hits), "global_line_coverage": len(line_hits) / len(unique_lines) if unique_lines else 0.0,
            "seed_count": row["seed_count"], "cov_json": row["cov_json"], "report_txt": row["report_txt"],
        })
    fields = list(result[0]) if result else ["run_id", "checkpoint_min", "status"]
    with (args.replay_dir / "coverage_summary.csv").open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields); writer.writeheader(); writer.writerows(result)
    (args.replay_dir / "coverage_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# SQLite unified LLVM checkpoint replay", ""]
    for row in result:
        lines.append(f"- {row['checkpoint_min']} min: target-region branch={row['target_region_branch_coverage']:.6f}, target hit={row['target_hit_rate']:.6f}, global branch={row['global_branch_coverage']:.6f}, global line={row['global_line_coverage']:.6f}, seeds={row['seed_count']}")
    (args.replay_dir / "coverage_summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"replay_dir": str(args.replay_dir), "completed_checkpoints": len(result), "summary": str(args.replay_dir / "coverage_summary.csv")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
