#!/usr/bin/env python3
"""Rebuild PostgreSQL RQ2 coverage CSVs from retained LLVM profdata files."""
from __future__ import annotations

import argparse
import csv
import json
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
    p.add_argument("--run-dir", type=Path, required=True, help="Replay output directory containing replay_index.tsv.")
    p.add_argument("--out", type=Path, help="Output data directory. Defaults to RUN_DIR/data.")
    p.add_argument("--target-regions", type=Path, help="target_regions.csv. Defaults to RUN_DIR/target_regions.csv.")
    p.add_argument("--image", default="griffin_postgres_llvmcov")
    p.add_argument("--binary", default="/root/bin_aflpp/usr/local/pgsql/bin/postgres")
    p.add_argument("--extra-object", action="append", default=["/root/bin_aflpp/usr/local/pgsql/lib/plpgsql.so"])
    p.add_argument("--source-root", default="/root/postgres")
    p.add_argument("--tmp-dir", type=Path, help="Host temp dir for per-checkpoint exports.")
    p.add_argument("--tool", default="SQUIRREL")
    p.add_argument("--keep-json", action="store_true")
    return p.parse_args()


def profdata_for(row: dict[str, str]) -> Path:
    cov_json = Path(row.get("cov_json") or "")
    if cov_json.name.endswith(".cov.json"):
        return cov_json.with_name(cov_json.name[: -len(".cov.json")] + ".profdata")
    report = Path(row.get("report_txt") or "")
    if report.name.endswith(".report.txt"):
        return report.with_name(report.name[: -len(".report.txt")] + ".profdata")
    return cov_json.with_suffix(".profdata")


def docker_export(
    *,
    image: str,
    binary: str,
    objects: list[str],
    profdata: Path,
    source_files: list[str],
    tmp_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    target_json = tmp_dir / f"{stem}.target.cov.json"
    summary_json = tmp_dir / f"{stem}.summary.cov.json"
    object_args = " ".join(f"-object={shlex.quote(obj)}" for obj in objects)
    sources = " ".join(shlex.quote(src) for src in source_files)
    script = f"""
set -e
llvm-cov-12 export -format=text {shlex.quote(binary)} {object_args} \\
  -instr-profile=/rq2_prof/{shlex.quote(profdata.name)} {sources} > /rq2_cov/{shlex.quote(target_json.name)}
llvm-cov-12 export -format=text --summary-only {shlex.quote(binary)} {object_args} \\
  -instr-profile=/rq2_prof/{shlex.quote(profdata.name)} > /rq2_cov/{shlex.quote(summary_json.name)}
"""
    cmd = [
        "docker",
        "run",
        "--rm",
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
    return target_json, summary_json


def global_branch_totals(summary_json: Path) -> tuple[int, int]:
    obj = json.loads(summary_json.read_text(errors="replace"))
    data = obj.get("data") or []
    totals = (data[0].get("totals") or {}).get("branches") if data else {}
    total = int(totals.get("count") or 0)
    covered = int(totals.get("covered") or 0)
    return total, covered


def append_run_summary_fields(
    final: dict[str, object], timeseries: list[dict[str, object]], run_id: str
) -> dict[str, object]:
    run_ts = sorted([r for r in timeseries if r["run_id"] == run_id], key=lambda r: int(r["elapsed_min"]))
    final["time_to_first_risk_target_sec"] = next(
        (int(r["elapsed_min"]) * 60 for r in run_ts if int(r["risk_branches_hit"]) > 0), ""
    )
    final["time_to_50pct_risk_targets_sec"] = next(
        (int(r["elapsed_min"]) * 60 for r in run_ts if float(r["target_region_branch_coverage"]) >= 0.5), ""
    )
    return final


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir
    out = args.out or run_dir / "data"
    target_regions = args.target_regions or run_dir / "target_regions.csv"
    tmp_dir = args.tmp_dir or run_dir / "tmp_resummarize_pg"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    raw_regions = cov.read_rows(target_regions)
    regions = [row for row in raw_regions if cov.valid_region(row)]
    skipped_regions = len(raw_regions) - len(regions)
    if skipped_regions:
        print(f"skipped_invalid_target_regions={skipped_regions}")

    source_files = sorted({str(Path(args.source_root) / r["source_path"]) for r in regions if r["dbms"] == "postgres"})
    if not source_files:
        raise SystemExit("no postgres target source files")
    print("target_source_files=" + ",".join(source_files))

    index = cov.read_rows(run_dir / "replay_index.tsv", "\t")
    runs: dict[str, dict[str, object]] = {}
    timeseries: list[dict[str, object]] = []
    final_candidates: dict[str, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    all_target_rows: list[dict[str, object]] = []
    all_branch_rows: list[dict[str, object]] = []
    comp_by_run: list[dict[str, object]] = []

    complete_rows = [
        row
        for row in index
        if row.get("status") == "complete" and row.get("dbms") == "postgres" and profdata_for(row).exists()
    ]
    print(f"complete_profdata_rows={len(complete_rows)}")

    for n, row in enumerate(complete_rows, start=1):
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
        profdata = profdata_for(row)
        elapsed = int(row.get("checkpoint_min") or 0)
        stem = f"{row['run_id']}_t{elapsed}"
        print(f"[{n}/{len(complete_rows)}] export {stem}", flush=True)
        target_json, summary_json = docker_export(
            image=args.image,
            binary=args.binary,
            objects=args.extra_object,
            profdata=profdata,
            source_files=source_files,
            tmp_dir=tmp_dir,
            stem=stem,
        )
        try:
            row_for_summary = dict(row)
            row_for_summary["cov_json"] = str(target_json)
            base, target_rows, branch_rows, comp_rows = cov.summarize_one(row_for_summary, regions, {})
            global_total, global_hit = global_branch_totals(summary_json)
            base["global_branches_total"] = global_total
            base["global_branches_hit"] = global_hit
            base["global_branch_coverage"] = global_hit / global_total if global_total else 0.0
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
        finally:
            if not args.keep_json:
                target_json.unlink(missing_ok=True)
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

    out.mkdir(parents=True, exist_ok=True)
    cov.write_csv(out / "runs.csv", list(runs.values()), cov.RUN_FIELDS)
    cov.write_csv(out / "coverage_summary.csv", summary, cov.SUMMARY_FIELDS)
    cov.write_csv(
        out / "coverage_timeseries.csv",
        sorted(timeseries, key=lambda r: (str(r["dbms"]), int(r["repeat_id"]), int(r["elapsed_min"]))),
        cov.TS_FIELDS,
    )
    cov.write_csv(
        out / "target_region_hits.csv",
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
        out / "target_branch_hits.csv",
        all_branch_rows,
        ["run_id", "dbms", "repeat_id", "branch_id", "file", "start_line", "end_line", "count", "hit", "region_ids"],
    )
    cov.write_csv(
        out / "component_heatmap_by_run.csv",
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
        out / "component_heatmap.csv",
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
    if not args.keep_json:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
