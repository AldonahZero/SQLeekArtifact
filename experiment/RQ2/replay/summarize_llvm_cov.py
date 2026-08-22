#!/usr/bin/env python3
"""Summarize LLVM source coverage exports into RQ2 CSV files."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path

SUMMARY_FIELDS = [
    "run_id",
    "tool",
    "dbms",
    "repeat_id",
    "risk_branches_total",
    "risk_branches_hit",
    "target_region_branch_coverage",
    "risk_targets_total",
    "risk_targets_hit",
    "target_function_hit_rate",
    "global_branches_total",
    "global_branches_hit",
    "global_branch_coverage",
    "time_to_first_risk_target_sec",
    "time_to_50pct_risk_targets_sec",
]
TS_FIELDS = [
    "run_id",
    "tool",
    "dbms",
    "repeat_id",
    "elapsed_min",
    "risk_branches_hit",
    "target_region_branch_coverage",
    "risk_targets_hit",
    "target_function_hit_rate",
    "global_branches_hit",
    "global_branch_coverage",
]
RUN_FIELDS = [
    "run_id",
    "tool",
    "dbms",
    "version",
    "repeat_id",
    "start_time",
    "end_time",
    "budget_hours",
    "seed_corpus",
    "build_id",
    "container_id",
    "status",
    "unsupported_reason",
]
REGION_BIN_SIZE = 256


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-regions", type=Path, required=True)
    p.add_argument("--replay-index", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--tool", default="SQUIRREL")
    p.add_argument(
        "--sqlite-amalgamation",
        type=Path,
        default=Path(os.environ["SQLITE_AMALGAMATION"]) if os.environ.get("SQLITE_AMALGAMATION") else None,
        help="sqlite3.c used by the LLVM coverage build; maps split SQLite source regions into amalgamation lines.",
    )
    return p.parse_args()


def read_rows(path: Path, delimiter: str | None = None) -> list[dict[str, str]]:
    if delimiter is None:
        delimiter = "\t" if path.suffix == ".tsv" else ","
    if not path.exists():
        return []
    with path.open(newline="") as fp:
        return list(csv.DictReader(fp, delimiter=delimiter))


def valid_region(row: dict[str, str]) -> bool:
    try:
        return int(row["start_line"]) <= int(row["end_line"])
    except Exception:
        return False


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def norm_file(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def same_file(cov_file: str, region: dict[str, str]) -> bool:
    cf = norm_file(cov_file)
    sp = norm_file(region["source_path"])
    return cf == sp or cf.endswith("/" + sp) or Path(cf).name == region["file"]


def sqlite_amalgamation_offsets(path: Path | None) -> dict[str, int]:
    """Return original SQLite source basename -> marker line in sqlite3.c."""
    if not path or not path.exists():
        return {}
    marker = re.compile(r"/\*+\s*Begin file\s+([^*\s]+)\s*\*+/")
    offsets: dict[str, int] = {}
    for line_no, raw in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        m = marker.search(raw)
        if not m:
            continue
        offsets[Path(m.group(1)).name] = line_no
    return offsets


def sqlite_region_match_view(
    dbms: str,
    regions: list[dict[str, str]],
    coverage_files: set[str],
    sqlite_offsets: dict[str, int],
) -> list[dict[str, str]]:
    if dbms != "sqlite" or not sqlite_offsets:
        return regions
    has_amalgamation = any(Path(norm_file(path)).name == "sqlite3.c" for path in coverage_files)
    if not has_amalgamation:
        return regions
    mapped: list[dict[str, str]] = []
    for region in regions:
        mapped.append(region)
        source_name = Path(norm_file(region.get("source_path") or region.get("file") or "")).name
        if source_name == "sqlite3.c" or source_name not in sqlite_offsets:
            continue
        try:
            offset = sqlite_offsets[source_name]
            start_line = int(region["start_line"])
            end_line = int(region["end_line"])
        except Exception:
            continue
        rr = dict(region)
        rr["file"] = "sqlite3.c"
        rr["source_path"] = "sqlite3.c"
        rr["start_line"] = str(offset + start_line)
        rr["end_line"] = str(offset + end_line)
        mapped.append(rr)
    return mapped


def build_region_bins(
    coverage_files: set[str], regions: list[dict[str, str]], bin_size: int = REGION_BIN_SIZE
) -> dict[str, dict[int, list[dict[str, str]]]]:
    by_file: dict[str, dict[int, list[dict[str, str]]]] = {}
    for cov_file in coverage_files:
        bins: dict[int, list[dict[str, str]]] = defaultdict(list)
        for region in regions:
            if not same_file(cov_file, region):
                continue
            try:
                start = int(region["start_line"])
                end = int(region["end_line"])
            except Exception:
                continue
            for bucket in range(start // bin_size, end // bin_size + 1):
                bins[bucket].append(region)
        by_file[cov_file] = bins
    return by_file


def regions_for_interval(
    region_bins: dict[str, dict[int, list[dict[str, str]]]],
    cov_file: str,
    start: int,
    end: int,
    bin_size: int = REGION_BIN_SIZE,
):
    bins = region_bins.get(cov_file)
    if not bins:
        return
    seen: set[int] = set()
    for bucket in range(start // bin_size, end // bin_size + 1):
        for region in bins.get(bucket, []):
            region_key = id(region)
            if region_key in seen:
                continue
            seen.add(region_key)
            if intersects(start, end, int(region["start_line"]), int(region["end_line"])):
                yield region


def hit_segment_lines_by_file(segments: list[dict[str, object]]) -> dict[str, list[int]]:
    by_file: dict[str, set[int]] = defaultdict(set)
    for segment in segments:
        if int(segment["count"]) <= 0:
            continue
        by_file[str(segment["file"])].add(int(segment["start_line"]))
    return {path: sorted(lines) for path, lines in by_file.items()}


def interval_has_hit(lines: list[int], start: int, end: int) -> bool:
    idx = bisect.bisect_left(lines, start)
    return idx < len(lines) and lines[idx] <= end


def intersects(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 <= b1 and a1 >= b0


def parse_coverage(path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    obj = json.loads(path.read_text(errors="replace"))
    data = obj.get("data") or []
    if not data:
        return [], []
    branches: list[dict[str, object]] = []
    segments: list[dict[str, object]] = []
    for fidx, fobj in enumerate(data[0].get("files") or []):
        filename = fobj.get("filename") or fobj.get("name") or ""
        for bidx, branch in enumerate(fobj.get("branches") or []):
            if len(branch) < 5:
                continue
            try:
                start = int(branch[0])
                end = int(branch[2]) if int(branch[2]) > 0 else start
                true_count = int(branch[4])
                false_count = int(branch[5]) if len(branch) > 5 else None
            except Exception:
                continue
            outcomes = [("T", true_count)]
            if false_count is not None:
                outcomes.append(("F", false_count))
            for outcome, count in outcomes:
                branches.append(
                    {
                        "id": f"{fidx}:{bidx}:{outcome}",
                        "file": filename,
                        "start_line": start,
                        "end_line": end,
                        "count": count,
                    }
                )
        for sidx, segment in enumerate(fobj.get("segments") or []):
            if len(segment) < 3:
                continue
            try:
                line = int(segment[0])
                count = int(segment[2])
            except Exception:
                continue
            has_count = bool(segment[3]) if len(segment) > 3 else True
            if has_count:
                segments.append({"id": f"{fidx}:{sidx}", "file": filename, "start_line": line, "end_line": line, "count": count})
    return branches, segments


def summarize_one(
    index_row: dict[str, str], regions: list[dict[str, str]], sqlite_offsets: dict[str, int]
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    branches, segments = parse_coverage(Path(index_row["cov_json"]))
    dbms = index_row["dbms"]
    db_regions = [r for r in regions if r["dbms"] == dbms]
    coverage_files = {str(x["file"]) for x in branches + segments}
    match_regions = sqlite_region_match_view(dbms, db_regions, coverage_files, sqlite_offsets)
    region_bins = build_region_bins(coverage_files, match_regions)
    global_total = len(branches)
    global_hit = len({b["id"] for b in branches if int(b["count"]) > 0})

    risk: dict[str, dict[str, object]] = {}
    region_hit = {r["region_id"]: False for r in db_regions}
    function_total = {r["function"] or r["region_id"] for r in db_regions}
    function_hit: set[str] = set()
    comp: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"total": set(), "hit": set(), "targets": set(), "target_hit": set(), "functions": set(), "function_hit": set()}
    )

    for branch in branches:
        start_line = int(branch["start_line"])
        end_line = int(branch["end_line"])
        for region in regions_for_interval(region_bins, str(branch["file"]), start_line, end_line):
            branch_id = f"{norm_file(str(branch['file']))}:{branch['start_line']}:{branch['end_line']}:{branch['id']}"
            hit = int(branch["count"]) > 0
            risk.setdefault(
                branch_id,
                {
                    "branch_id": branch_id,
                    "file": branch["file"],
                    "start_line": branch["start_line"],
                    "end_line": branch["end_line"],
                    "count": 0,
                    "regions": set(),
                    "hit": False,
                },
            )
            risk[branch_id]["count"] = max(int(risk[branch_id]["count"]), int(branch["count"]))
            risk[branch_id]["hit"] = bool(risk[branch_id]["hit"]) or hit
            risk[branch_id]["regions"].add(region["region_id"])
            c = comp[region["component"]]
            c["total"].add(branch_id)
            c["targets"].add(region["region_id"])
            c["functions"].add(region["function"] or region["region_id"])
            if hit:
                region_hit[region["region_id"]] = True
                function_hit.add(region["function"] or region["region_id"])
                c["hit"].add(branch_id)
                c["target_hit"].add(region["region_id"])
                c["function_hit"].add(region["function"] or region["region_id"])

    # Segments do not inflate branch denominators, but they help audit target/function hits.
    hit_lines = hit_segment_lines_by_file(segments)
    for region in match_regions:
        try:
            start_line = int(region["start_line"])
            end_line = int(region["end_line"])
        except Exception:
            continue
        for cov_file, lines in hit_lines.items():
            if same_file(cov_file, region) and interval_has_hit(lines, start_line, end_line):
                region_hit[region["region_id"]] = True
                function_hit.add(region["function"] or region["region_id"])
                break

    risk_total = len(risk)
    risk_hit = sum(1 for v in risk.values() if v["hit"])
    target_total = len(db_regions)
    target_hit = sum(1 for v in region_hit.values() if v)
    fn_total = len(function_total)
    fn_hit = len(function_hit)
    base = {
        "run_id": index_row["run_id"],
        "tool": index_row.get("tool") or "SQUIRREL",
        "dbms": dbms,
        "repeat_id": index_row["repeat_id"],
        "risk_branches_total": risk_total,
        "risk_branches_hit": risk_hit,
        "target_region_branch_coverage": risk_hit / risk_total if risk_total else 0.0,
        "risk_targets_total": target_total,
        "risk_targets_hit": target_hit,
        "target_function_hit_rate": fn_hit / fn_total if fn_total else 0.0,
        "global_branches_total": global_total,
        "global_branches_hit": global_hit,
        "global_branch_coverage": global_hit / global_total if global_total else 0.0,
    }
    target_rows = [
        {
            "run_id": index_row["run_id"],
            "dbms": dbms,
            "repeat_id": index_row["repeat_id"],
            **region,
            "hit": int(region_hit[region["region_id"]]),
        }
        for region in db_regions
    ]
    branch_rows = [
        {
            "run_id": index_row["run_id"],
            "dbms": dbms,
            "repeat_id": index_row["repeat_id"],
            "branch_id": v["branch_id"],
            "file": v["file"],
            "start_line": v["start_line"],
            "end_line": v["end_line"],
            "count": v["count"],
            "hit": int(v["hit"]),
            "region_ids": ";".join(sorted(v["regions"])),
        }
        for v in risk.values()
    ]
    comp_rows = []
    for component, values in comp.items():
        total = len(values["total"])
        hit = len(values["hit"])
        ft = len(values["functions"])
        fh = len(values["function_hit"])
        comp_rows.append(
            {
                "run_id": index_row["run_id"],
                "dbms": dbms,
                "repeat_id": index_row["repeat_id"],
                "component": component,
                "tool": index_row.get("tool") or "SQUIRREL",
                "risk_branches_total": total,
                "risk_branches_hit": hit,
                "target_region_branch_coverage": hit / total if total else 0.0,
                "risk_targets_total": len(values["targets"]),
                "risk_targets_hit": len(values["target_hit"]),
                "target_function_hit_rate": fh / ft if ft else 0.0,
            }
        )
    return base, target_rows, branch_rows, comp_rows


def main() -> None:
    args = parse_args()
    raw_regions = read_rows(args.target_regions)
    regions = [row for row in raw_regions if valid_region(row)]
    skipped_regions = len(raw_regions) - len(regions)
    if skipped_regions:
        print(f"skipped_invalid_target_regions={skipped_regions}")
    index = read_rows(args.replay_index, "\t")
    sqlite_offsets = sqlite_amalgamation_offsets(args.sqlite_amalgamation)
    if args.sqlite_amalgamation:
        print(f"sqlite_amalgamation_offsets={len(sqlite_offsets)} path={args.sqlite_amalgamation}")
    runs: dict[str, dict[str, object]] = {}
    timeseries: list[dict[str, object]] = []
    final_candidates: dict[str, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    all_target_rows: list[dict[str, object]] = []
    all_branch_rows: list[dict[str, object]] = []
    comp_by_run: list[dict[str, object]] = []

    for row in index:
        status = row.get("status", "")
        runs.setdefault(
            row["run_id"],
            {
                "run_id": row["run_id"],
                "tool": row.get("tool") or args.tool,
                "dbms": row.get("dbms", ""),
                "version": row.get("version", ""),
                "repeat_id": row.get("repeat_id", ""),
                "start_time": row.get("start_time", ""),
                "end_time": row.get("end_time", ""),
                "budget_hours": 24,
                "seed_corpus": row.get("seed_corpus", ""),
                "build_id": row.get("build_id", ""),
                "container_id": row.get("container_id", ""),
                "status": "completed" if status == "complete" else status,
                "unsupported_reason": row.get("message", "") if status != "complete" else "",
            },
        )
        if status != "complete" or not row.get("cov_json") or not Path(row["cov_json"]).exists():
            continue
        base, target_rows, branch_rows, comp_rows = summarize_one(row, regions, sqlite_offsets)
        elapsed = int(row.get("checkpoint_min") or 0)
        timeseries.append(
            {
                "run_id": base["run_id"],
                "tool": base["tool"],
                "dbms": base["dbms"],
                "repeat_id": base["repeat_id"],
                "elapsed_min": elapsed,
                "risk_branches_hit": base["risk_branches_hit"],
                "target_region_branch_coverage": base["target_region_branch_coverage"],
                "risk_targets_hit": base["risk_targets_hit"],
                "target_function_hit_rate": base["target_function_hit_rate"],
                "global_branches_hit": base["global_branches_hit"],
                "global_branch_coverage": base["global_branch_coverage"],
            }
        )
        final_candidates[row["run_id"]].append((elapsed, base))
        if elapsed >= 1440:
            all_target_rows.extend(target_rows)
            all_branch_rows.extend(branch_rows)
            comp_by_run.extend(comp_rows)

    summary: list[dict[str, object]] = []
    for run_id, vals in final_candidates.items():
        vals.sort(key=lambda x: x[0])
        final = dict(vals[-1][1])
        run_ts = sorted([r for r in timeseries if r["run_id"] == run_id], key=lambda r: int(r["elapsed_min"]))
        final["time_to_first_risk_target_sec"] = next(
            (int(r["elapsed_min"]) * 60 for r in run_ts if int(r["risk_branches_hit"]) > 0), ""
        )
        final["time_to_50pct_risk_targets_sec"] = next(
            (int(r["elapsed_min"]) * 60 for r in run_ts if float(r["target_region_branch_coverage"]) >= 0.5), ""
        )
        summary.append(final)

    comp_agg: list[dict[str, object]] = []
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in comp_by_run:
        groups[(str(row["dbms"]), str(row["component"]), str(row["tool"]))].append(row)
    for (dbms, component, tool), rows in sorted(groups.items()):
        total = max(int(r["risk_branches_total"]) for r in rows) if rows else 0
        hit = sum(float(r["risk_branches_hit"]) for r in rows) / len(rows) if rows else 0.0
        tt = max(int(r["risk_targets_total"]) for r in rows) if rows else 0
        th = sum(float(r["risk_targets_hit"]) for r in rows) / len(rows) if rows else 0.0
        rate = sum(float(r["target_function_hit_rate"]) for r in rows) / len(rows) if rows else 0.0
        comp_agg.append(
            {
                "dbms": dbms,
                "component": component,
                "tool": tool,
                "risk_branches_total": total,
                "risk_branches_hit": round(hit, 3),
                "target_region_branch_coverage": hit / total if total else 0.0,
                "risk_targets_total": tt,
                "risk_targets_hit": round(th, 3),
                "target_function_hit_rate": rate,
            }
        )

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "runs.csv", list(runs.values()), RUN_FIELDS)
    write_csv(args.out / "coverage_summary.csv", summary, SUMMARY_FIELDS)
    write_csv(
        args.out / "coverage_timeseries.csv",
        sorted(timeseries, key=lambda r: (str(r["dbms"]), int(r["repeat_id"]), int(r["elapsed_min"]))),
        TS_FIELDS,
    )
    write_csv(
        args.out / "target_region_hits.csv",
        all_target_rows,
        [
            "run_id",
            "dbms",
            "repeat_id",
            "region_id",
            "rule_family",
            "component",
            "file",
            "source_path",
            "anchor_file",
            "anchor_line",
            "start_line",
            "end_line",
            "function",
            "span_source",
            "hit",
        ],
    )
    write_csv(
        args.out / "target_branch_hits.csv",
        all_branch_rows,
        ["run_id", "dbms", "repeat_id", "branch_id", "file", "start_line", "end_line", "count", "hit", "region_ids"],
    )
    write_csv(
        args.out / "component_heatmap_by_run.csv",
        comp_by_run,
        [
            "run_id",
            "dbms",
            "repeat_id",
            "component",
            "tool",
            "risk_branches_total",
            "risk_branches_hit",
            "target_region_branch_coverage",
            "risk_targets_total",
            "risk_targets_hit",
            "target_function_hit_rate",
        ],
    )
    write_csv(
        args.out / "component_heatmap.csv",
        comp_agg,
        [
            "dbms",
            "component",
            "tool",
            "risk_branches_total",
            "risk_branches_hit",
            "target_region_branch_coverage",
            "risk_targets_total",
            "risk_targets_hit",
            "target_function_hit_rate",
        ],
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
