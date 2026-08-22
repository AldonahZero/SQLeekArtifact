#!/usr/bin/env python3
"""Build MariaDB RQ2 coverage CSVs from retained LLVM profdata files."""
from __future__ import annotations

import argparse
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import summarize_llvm_cov as cov  # noqa: E402
import resummarize_mysql_from_profdata as mysql_cov  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--target-regions", type=Path, required=True)
    p.add_argument("--profdata-root", type=Path, action="append", required=True)
    p.add_argument("--image", default="griffin_mariadb_llvmcov:latest")
    p.add_argument("--binary", default="/usr/local/mysql/bin/mariadbd")
    p.add_argument("--source-root", default="/root/mariadb")
    p.add_argument("--tmp-dir", type=Path)
    p.add_argument("--tool", default="SQUIRREL")
    p.add_argument(
        "--profdata-prefix",
        help="Filename prefix used by retained profdata files, e.g. squirrel_mariadb_r2_t60.profdata.",
    )
    p.add_argument("--repeats", default="1,2")
    p.add_argument("--keep-exports", action="store_true")
    return p.parse_args()


def requested_repeats(raw: str) -> list[int]:
    repeats = [int(item.strip()) for item in raw.split(",") if item.strip()]
    if not repeats:
        raise SystemExit("--repeats must contain at least one repeat id")
    return repeats


def discover_profdata(roots: list[Path], repeats: list[int], tool: str, profdata_prefix: str | None) -> dict[int, dict[int, Path]]:
    paths: dict[int, dict[int, Path]] = defaultdict(dict)
    prefix = profdata_prefix or ("squirrel" if tool.lower() == "squirrel" else tool.lower())
    for base in roots:
        for path in sorted(base.glob(f"r*/{prefix}_mariadb_r*_t*.profdata")):
            m = re.search(rf"{re.escape(prefix)}_mariadb_r(\d+)_t(\d+)\.profdata$", path.name)
            if not m:
                continue
            repeat = int(m.group(1))
            checkpoint = int(m.group(2))
            paths[repeat][checkpoint] = path
    missing = [str(i) for i in repeats if i not in paths]
    if missing:
        raise SystemExit(f"missing MariaDB profdata repeat(s): {', '.join(missing)}")
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
llvm-cov-12 export -format=lcov {shlex.quote(binary)} \\
  -instr-profile=/rq2_prof/{shlex.quote(profdata.name)} {sources} > /rq2_cov/{shlex.quote(lcov_path.name)}
llvm-cov-12 export -format=text --summary-only {shlex.quote(binary)} \\
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
    roots = [p.resolve() for p in args.profdata_root]
    repeats = requested_repeats(args.repeats)
    tmp_dir = (args.tmp_dir.resolve() if args.tmp_dir else args.out_dir / "tmp_mariadb_lcov")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    raw_regions = cov.read_rows(args.target_regions)
    regions = [row for row in raw_regions if cov.valid_region(row) and row.get("dbms") == "mariadb"]
    if not regions:
        raise SystemExit("no valid mariadb target regions")
    source_files = sorted({str(Path(args.source_root) / r["source_path"]) for r in regions})
    print(f"target_regions={len(regions)} target_source_files={len(source_files)}")
    if args.target_regions.resolve() != (args.out_dir / "target_regions.csv").resolve():
        shutil.copy2(args.target_regions, args.out_dir / "target_regions.csv")

    profdata = discover_profdata(roots, repeats, args.tool, args.profdata_prefix)
    runs: dict[str, dict[str, object]] = {}
    timeseries: list[dict[str, object]] = []
    final_candidates: dict[str, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    all_target_rows: list[dict[str, object]] = []
    all_branch_rows: list[dict[str, object]] = []
    comp_by_run: list[dict[str, object]] = []
    index_rows: list[dict[str, object]] = []

    jobs = sum(len(checkpoints) for checkpoints in profdata.values())
    job_idx = 0
    tool_slug = args.tool.strip().lower()
    for repeat, checkpoints in profdata.items():
        for checkpoint, prof in checkpoints.items():
            job_idx += 1
            run_id = f"mariadb_{tool_slug}_r{repeat}"
            stem = f"{run_id}_t{checkpoint}"
            report = prof.with_name(prof.name.replace(".profdata", ".report.txt"))
            row = {
                "run_id": run_id,
                "tool": args.tool,
                "dbms": "mariadb",
                "repeat_id": str(repeat),
                "checkpoint_min": str(checkpoint),
                "status": "complete",
                "cov_json": str(args.out_dir / f"{stem}.target.lcov"),
                "report_txt": str(report),
                "version": "MariaDB 12.3.2 llvmcov binary-mode replay",
                "start_time": "",
                "end_time": "",
                "seed_corpus": str(prof.parent),
                "build_id": "griffin_mariadb_llvmcov:latest binary-mode=1",
                "container_id": "",
                "message": "",
            }
            index_rows.append(row)
            runs[run_id] = {
                "run_id": run_id,
                "tool": args.tool,
                "dbms": "mariadb",
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

            print(f"[{job_idx}/{jobs}] export {stem}", flush=True)
            lcov_path, summary_json = docker_export_lcov(
                image=args.image,
                binary=args.binary,
                profdata=prof,
                source_files=source_files,
                tmp_dir=tmp_dir,
                stem=stem,
            )
            try:
                global_total, global_hit = mysql_cov.global_branch_totals(summary_json)
                base, target_rows, branch_rows, comp_rows = mysql_cov.summarize_lcov(
                    row=row,
                    regions=regions,
                    lcov_path=lcov_path,
                    global_total=global_total,
                    global_hit=global_hit,
                )
                timeseries.append(
                    {
                        "run_id": base["run_id"],
                        "tool": base["tool"],
                        "dbms": base["dbms"],
                        "repeat_id": base["repeat_id"],
                        "elapsed_min": checkpoint,
                        "risk_branches_hit": base["risk_branches_hit"],
                        "target_region_branch_coverage": base["target_region_branch_coverage"],
                        "risk_targets_hit": base["risk_targets_hit"],
                        "target_function_hit_rate": base["target_function_hit_rate"],
                        "global_branches_hit": base["global_branches_hit"],
                        "global_branch_coverage": base["global_branch_coverage"],
                    }
                )
                final_candidates[run_id].append((checkpoint, base))
                if checkpoint >= 1440:
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

    summary = []
    for run_id, vals in final_candidates.items():
        vals.sort(key=lambda x: x[0])
        summary.append(mysql_cov.append_run_summary_fields(dict(vals[-1][1]), timeseries, run_id))

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
