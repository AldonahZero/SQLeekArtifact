#!/usr/bin/env python3
"""Build MySQL RQ2 coverage CSVs from retained LLVM profdata files.

MySQL's full text JSON export is too large for routine replay summarization.
This script exports target-source coverage in LCOV format, parses BRDA/DA
records for target-region metrics, and uses a summary-only JSON export for the
global branch denominator.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "replay"))

import summarize_llvm_cov as cov  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, required=True, help="Replay output directory to receive target-region CSVs.")
    p.add_argument("--target-regions", type=Path, required=True)
    p.add_argument("--old-replay-dir", type=Path, default=BASE_DIR / "replay/output/sqleek_mysql_clean_replay_20260629_155931")
    p.add_argument("--new-replay-dir", type=Path, default=BASE_DIR / "replay/output/sqleek_mysql_clean_replay_20260630_125357_r3_r5")
    p.add_argument(
        "--profdata-root",
        type=Path,
        action="append",
        help="root containing r*/{sqleek,squirrel}_mysql_r*_t*.profdata; may be repeated",
    )
    p.add_argument("--image", default="griffin_mysql_clean_llvmcov")
    p.add_argument("--binary", default="/opt/mysql-llvmcov/bin/mysqld")
    p.add_argument("--source-root", default="/src/mysql-server")
    p.add_argument("--tmp-dir", type=Path)
    p.add_argument("--tool", default="SQLeek")
    p.add_argument("--repeats", default="1,2,3,4,5", help="Comma-separated repeat ids to summarize.")
    p.add_argument("--keep-exports", action="store_true")
    return p.parse_args()


def requested_repeats(args: argparse.Namespace) -> list[int]:
    repeats: list[int] = []
    for item in str(args.repeats).split(","):
        item = item.strip()
        if not item:
            continue
        repeats.append(int(item))
    if not repeats:
        raise SystemExit("--repeats must contain at least one repeat id")
    return repeats


def discover_profdata(args: argparse.Namespace) -> dict[int, dict[int, Path]]:
    paths: dict[int, dict[int, Path]] = defaultdict(dict)
    roots = [p.resolve() for p in args.profdata_root] if args.profdata_root else [args.old_replay_dir, args.new_replay_dir]
    repeats = requested_repeats(args)
    tool_slug = str(args.tool).strip().lower()
    if tool_slug == "squirrel":
        name_prefixes = ["squirrel"]
    elif tool_slug == "sqleek":
        name_prefixes = ["sqleek"]
    else:
        name_prefixes = [tool_slug]
    for base in roots:
        for prefix in name_prefixes:
            for path in sorted(base.glob(f"r*/{prefix}_mysql_r*_t*.profdata")):
                m = re.search(rf"{re.escape(prefix)}_mysql_r(\d+)_t(\d+)\.profdata$", path.name)
                if not m:
                    continue
                repeat = int(m.group(1))
                checkpoint = int(m.group(2))
                paths[repeat][checkpoint] = path
    missing = [str(i) for i in repeats if i not in paths]
    if missing:
        raise SystemExit(f"missing MySQL profdata repeat(s): {', '.join(missing)}")
    return {i: dict(sorted(paths[i].items())) for i in repeats}


def docker_export_lcov(
    *,
    image: str,
    binary: str,
    profdata: Path,
    source_files: list[str],
    tmp_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    lcov_path = tmp_dir / f"{stem}.target.lcov"
    summary_json = tmp_dir / f"{stem}.summary.cov.json"
    sources = " ".join(shlex.quote(src) for src in source_files)
    script = f"""
set -e
llvm-cov-18 export -format=lcov {shlex.quote(binary)} \\
  -instr-profile=/rq2_prof/{shlex.quote(profdata.name)} {sources} > /rq2_cov/{shlex.quote(lcov_path.name)}
llvm-cov-18 export -format=text --summary-only {shlex.quote(binary)} \\
  -instr-profile=/rq2_prof/{shlex.quote(profdata.name)} > /rq2_cov/{shlex.quote(summary_json.name)}
"""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--memory=24g",
        "--cpus=4",
        "-v",
        f"{profdata.parent}:/rq2_prof:ro",
        "-v",
        f"{tmp_dir}:/rq2_cov",
        "--entrypoint",
        "/bin/bash",
        image,
        "-lc",
        script,
    ]
    subprocess.run(cmd, check=True)
    return lcov_path, summary_json


def global_branch_totals(summary_json: Path) -> tuple[int, int]:
    obj = json.loads(summary_json.read_text(errors="replace"))
    data = obj.get("data") or []
    totals = (data[0].get("totals") or {}).get("branches") if data else {}
    total = int(totals.get("count") or 0)
    covered = int(totals.get("covered") or 0)
    return total, covered


def parse_lcov(path: Path) -> tuple[list[dict[str, object]], dict[str, list[int]]]:
    branches: list[dict[str, object]] = []
    hit_lines: dict[str, set[int]] = defaultdict(set)
    current_file = ""
    for raw in path.read_text(errors="replace").splitlines():
        if raw.startswith("SF:"):
            current_file = raw[3:]
            continue
        if raw.startswith("BRDA:") and current_file:
            parts = raw[5:].split(",")
            if len(parts) < 4:
                continue
            try:
                line = int(parts[0])
            except ValueError:
                continue
            count = 0 if parts[3] == "-" else int(parts[3])
            branches.append(
                {
                    "id": f"{cov.norm_file(current_file)}:{line}:{parts[1]}:{parts[2]}",
                    "file": current_file,
                    "start_line": line,
                    "end_line": line,
                    "count": count,
                }
            )
            continue
        if raw.startswith("DA:") and current_file:
            parts = raw[3:].split(",")
            if len(parts) < 2:
                continue
            try:
                line = int(parts[0])
                count = int(parts[1])
            except ValueError:
                continue
            if count > 0:
                hit_lines[current_file].add(line)
    return branches, {path: sorted(lines) for path, lines in hit_lines.items()}


def interval_has_hit(lines: list[int], start: int, end: int) -> bool:
    idx = bisect.bisect_left(lines, start)
    return idx < len(lines) and lines[idx] <= end


def summarize_lcov(
    *,
    row: dict[str, str],
    regions: list[dict[str, str]],
    lcov_path: Path,
    global_total: int,
    global_hit: int,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    branches, hit_lines = parse_lcov(lcov_path)
    dbms = row["dbms"]
    db_regions = [r for r in regions if r["dbms"] == dbms]
    coverage_files = {str(b["file"]) for b in branches} | set(hit_lines)
    region_bins = cov.build_region_bins(coverage_files, db_regions)

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
        for region in cov.regions_for_interval(region_bins, str(branch["file"]), start_line, end_line):
            branch_id = str(branch["id"])
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

    for region in db_regions:
        try:
            start_line = int(region["start_line"])
            end_line = int(region["end_line"])
        except Exception:
            continue
        for cov_file, lines in hit_lines.items():
            if cov.same_file(cov_file, region) and interval_has_hit(lines, start_line, end_line):
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
        "run_id": row["run_id"],
        "tool": row.get("tool") or "SQLeek",
        "dbms": dbms,
        "repeat_id": row["repeat_id"],
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
            "run_id": row["run_id"],
            "dbms": dbms,
            "repeat_id": row["repeat_id"],
            **region,
            "hit": int(region_hit[region["region_id"]]),
        }
        for region in db_regions
    ]
    branch_rows = [
        {
            "run_id": row["run_id"],
            "dbms": dbms,
            "repeat_id": row["repeat_id"],
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
                "run_id": row["run_id"],
                "dbms": dbms,
                "repeat_id": row["repeat_id"],
                "component": component,
                "tool": row.get("tool") or "SQLeek",
                "risk_branches_total": total,
                "risk_branches_hit": hit,
                "target_region_branch_coverage": hit / total if total else 0.0,
                "risk_targets_total": len(values["targets"]),
                "risk_targets_hit": len(values["target_hit"]),
                "target_function_hit_rate": fh / ft if ft else 0.0,
            }
        )
    return base, target_rows, branch_rows, comp_rows


def append_run_summary_fields(final: dict[str, object], timeseries: list[dict[str, object]], run_id: str) -> dict[str, object]:
    run_ts = sorted([r for r in timeseries if r["run_id"] == run_id], key=lambda r: int(r["elapsed_min"]))
    final["time_to_first_risk_target_sec"] = next(
        (int(r["elapsed_min"]) * 60 for r in run_ts if int(r["risk_branches_hit"]) > 0), ""
    )
    final["time_to_50pct_risk_targets_sec"] = next(
        (int(r["elapsed_min"]) * 60 for r in run_ts if float(r["target_region_branch_coverage"]) >= 0.5), ""
    )
    return final


def write_replay_index(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "run_id",
        "tool",
        "dbms",
        "repeat_id",
        "checkpoint_min",
        "status",
        "cov_json",
        "report_txt",
        "version",
        "start_time",
        "end_time",
        "seed_corpus",
        "build_id",
        "container_id",
        "message",
    ]
    with path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    args.out_dir = args.out_dir.resolve()
    args.target_regions = args.target_regions.resolve()
    args.old_replay_dir = args.old_replay_dir.resolve()
    args.new_replay_dir = args.new_replay_dir.resolve()
    if args.profdata_root:
        args.profdata_root = [p.resolve() for p in args.profdata_root]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = (args.tmp_dir.resolve() if args.tmp_dir else args.out_dir / "tmp_mysql_lcov")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    raw_regions = cov.read_rows(args.target_regions)
    regions = [row for row in raw_regions if cov.valid_region(row) and row.get("dbms") == "mysql"]
    if not regions:
        raise SystemExit("no valid mysql target regions")
    source_files = sorted({str(Path(args.source_root) / r["source_path"]) for r in regions})
    print(f"target_regions={len(regions)} target_source_files={len(source_files)}")

    shutil.copy2(args.target_regions, args.out_dir / "target_regions.csv")
    profdata = discover_profdata(args)

    runs: dict[str, dict[str, object]] = {}
    timeseries: list[dict[str, object]] = []
    final_candidates: dict[str, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    all_target_rows: list[dict[str, object]] = []
    all_branch_rows: list[dict[str, object]] = []
    comp_by_run: list[dict[str, object]] = []
    index_rows: list[dict[str, object]] = []

    total_jobs = sum(len(checkpoints) for checkpoints in profdata.values())
    job_idx = 0
    tool_slug = str(args.tool).strip().lower()
    for repeat, checkpoints in profdata.items():
      for checkpoint, prof in checkpoints.items():
        job_idx += 1
        run_id = f"mysql_{tool_slug}_r{repeat}"
        stem = f"{run_id}_t{checkpoint}"
        report = prof.with_name(prof.name.replace(".profdata", ".report.txt"))
        row = {
            "run_id": run_id,
            "tool": args.tool,
            "dbms": "mysql",
            "repeat_id": str(repeat),
            "checkpoint_min": str(checkpoint),
            "status": "complete",
            "cov_json": str(args.out_dir / f"{stem}.target.lcov"),
            "report_txt": str(report),
            "version": "MySQL clean llvmcov",
            "start_time": "",
            "end_time": "",
            "seed_corpus": str(prof.parent),
            "build_id": "griffin_mysql_clean_llvmcov",
            "container_id": "",
            "message": "",
        }
        index_rows.append(row)
        runs[run_id] = {
            "run_id": run_id,
            "tool": args.tool,
            "dbms": "mysql",
            "version": row["version"],
            "repeat_id": str(repeat),
            "start_time": "",
            "end_time": "",
            "budget_hours": 24,
            "seed_corpus": str(prof.parent),
            "build_id": row["build_id"],
            "container_id": "",
            "status": "completed",
            "unsupported_reason": "",
        }

        print(f"[{job_idx}/{total_jobs}] export {stem}", flush=True)
        lcov_path, summary_json = docker_export_lcov(
            image=args.image,
            binary=args.binary,
            profdata=prof,
            source_files=source_files,
            tmp_dir=tmp_dir,
            stem=stem,
        )
        try:
            global_total, global_hit = global_branch_totals(summary_json)
            base, target_rows, branch_rows, comp_rows = summarize_lcov(
                row=row,
                regions=regions,
                lcov_path=lcov_path,
                global_total=global_total,
                global_hit=global_hit,
            )
            elapsed = checkpoint
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
            final_candidates[run_id].append((elapsed, base))
            if elapsed >= 1440:
                all_target_rows.extend(target_rows)
                all_branch_rows.extend(branch_rows)
                comp_by_run.extend(comp_rows)
            print(
                f"  target_branches={base['risk_branches_hit']}/{base['risk_branches_total']} "
                f"target_regions={base['risk_targets_hit']}/{base['risk_targets_total']} "
                f"global_branches={base['global_branches_hit']}/{base['global_branches_total']}",
                flush=True,
            )
        finally:
            if not args.keep_exports:
                lcov_path.unlink(missing_ok=True)
                summary_json.unlink(missing_ok=True)

    summary: list[dict[str, object]] = []
    for run_id, vals in final_candidates.items():
        vals.sort(key=lambda x: x[0])
        summary.append(append_run_summary_fields(dict(vals[-1][1]), timeseries, run_id))

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

    cov.write_csv(args.out_dir / "runs.csv", list(runs.values()), cov.RUN_FIELDS)
    cov.write_csv(args.out_dir / "coverage_summary.csv", summary, cov.SUMMARY_FIELDS)
    cov.write_csv(
        args.out_dir / "coverage_timeseries.csv",
        sorted(timeseries, key=lambda r: (int(r["repeat_id"]), int(r["elapsed_min"]))),
        cov.TS_FIELDS,
    )
    cov.write_csv(
        args.out_dir / "target_region_hits.csv",
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
    cov.write_csv(
        args.out_dir / "target_branch_hits.csv",
        all_branch_rows,
        ["run_id", "dbms", "repeat_id", "branch_id", "file", "start_line", "end_line", "count", "hit", "region_ids"],
    )
    cov.write_csv(
        args.out_dir / "component_heatmap_by_run.csv",
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
    cov.write_csv(
        args.out_dir / "component_heatmap.csv",
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
    write_replay_index(args.out_dir / "replay_index.tsv", index_rows)
    if not args.keep_exports:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"wrote {args.out_dir}")


if __name__ == "__main__":
    main()
