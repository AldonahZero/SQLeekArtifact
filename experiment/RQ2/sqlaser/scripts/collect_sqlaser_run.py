#!/usr/bin/env python3
"""Collect a SQLaser formal run and materialize first-seen checkpoint corpora."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CHECKPOINTS = (60, 180, 300, 480, 600, 720, 900, 1200, 1440)


def json_read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def stats_read(path: Path) -> dict[str, str]:
    result = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return result
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def epoch(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def runtime_info(run: Path) -> dict:
    stats = stats_read(run / "afl_out" / "fuzzer_stats")
    manifest = json_read(run / "run_manifest.json")
    end_manifest = json_read(run / "end_manifest.json")
    start = float(stats.get("start_time", 0) or 0)
    if not start:
        start = epoch(str(manifest.get("start_time", ""))) or 0
    finish = epoch(str(end_manifest.get("end_time", "")))
    if finish is None:
        finish = float(stats.get("last_update", 0) or time.time())
    cutoff = int(manifest.get("cutoff_seconds", manifest.get("seconds_limit", 0)) or 0)
    runtime = max(0.0, finish - start) if start else 0.0
    return {
        "start_epoch": start,
        "end_epoch": finish,
        "runtime_seconds": runtime,
        "cutoff_seconds": cutoff,
        "runtime_tolerance_seconds": 300,
        "runtime_qualified": bool(cutoff and runtime >= cutoff - 300),
        "exit_code": (run / "exit_code").read_text(errors="replace").strip() if (run / "exit_code").exists() else None,
    }


def plot_last(path: Path) -> tuple[dict[str, str], list[str]]:
    try:
        lines = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]
    except FileNotFoundError:
        return {}, []
    if len(lines) < 2:
        return {}, []
    header = [item.strip() for item in lines[0].split(",")]
    return dict(zip(header, [item.strip() for item in lines[-1].split(",")])), header


def energy(progress: int, chain_len: int, distance: int, weight: int) -> int:
    if not chain_len:
        return 1
    rounds = 4 if distance == 0 else 3 if distance == 1 else 2 if distance == 2 or progress else 1
    return min(6, rounds * max(1, weight))


def distributions(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    distance = {}
    energies = {str(i): 0 for i in range(1, 7)}
    if not path.exists():
        return distance, energies
    with path.open(errors="replace", newline="") as fp:
        for row in csv.DictReader(fp, delimiter="\t"):
            try:
                d = int(row.get("distance", "")); p = int(row.get("progress", "0"))
                clen = int(row.get("chain_len", "0")); weight = max(1, int(row.get("weight", "1")))
            except ValueError:
                continue
            distance[str(d)] = distance.get(str(d), 0) + 1
            energies[str(energy(p, clen, d, weight))] += 1
    return distance, energies


def copy_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def collect_final(run: Path, info: dict, prepare_only: bool) -> None:
    final = run / "final"
    final.mkdir(parents=True, exist_ok=True)
    for name in ("fuzzer_stats", "plot_data", "sqlaser_seed_metadata.tsv"):
        copy_file(run / "afl_out" / name, final / name)
    for name in ("run_manifest.json", "end_manifest.json", "image_id.txt", "command.txt"):
        copy_file(run / name, final / name)
    logs = run / "container_logs"
    logs.mkdir(exist_ok=True)
    copy_file(run / "stdout.log", logs / "stdout.log")
    copy_file(run / "stderr.log", logs / "stderr.log")
    (logs / "README.txt").write_text("Captured docker stdout and stderr for this run.\n")
    plot, header = plot_last(run / "afl_out" / "plot_data")
    distance, energies = distributions(run / "afl_out" / "sqlaser_seed_metadata.tsv")
    chain = {key: plot.get(key) for key in header if key.startswith("sqlaser_")}
    oracle = {key: plot.get(key) for key in header if "oracle" in key.lower()}
    for name, value in (("distance_distribution.json", distance), ("energy_distribution.json", energies), ("chain_hit_statistics.json", chain), ("oracle_statistics.json", oracle)):
        (run / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        (final / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    collection = {
        "run_dir": str(run), "collected_at": datetime.now(timezone.utc).isoformat(), "prepare_only": prepare_only,
        "runtime": info,
        "artifacts": {
            "fuzzer_stats": str(final / "fuzzer_stats"), "plot_data": str(final / "plot_data"),
            "queue": str(run / "afl_out" / "queue"), "bugs": str(run / "bugs"),
            "hangs": str(run / "afl_out" / "hangs"), "crashes": str(run / "afl_out" / "crashes"),
            "sqlaser_seed_metadata": str(run / "afl_out" / "sqlaser_seed_metadata.tsv"), "container_logs": str(logs),
        }, "chain_hit_statistics": chain, "oracle_statistics": oracle,
        "distance_distribution": distance, "energy_distribution": energies,
    }
    (run / "collection_manifest.json").write_text(json.dumps(collection, indent=2, sort_keys=True) + "\n")


def material_name(index: int, offset_ms: int, source_name: str) -> str:
    digest = hashlib.sha256(source_name.encode("utf-8", "replace")).hexdigest()[:16]
    return f"id:{index:06d},time:{offset_ms},orig:{digest}.sql"


def prepare_checkpoints(run: Path, checkpoints: tuple[int, ...]) -> None:
    root = run / "checkpoint_replay"
    root.mkdir(parents=True, exist_ok=True)
    source = run / "afl_out" / "queue"
    start = float(stats_read(run / "afl_out" / "fuzzer_stats").get("start_time", 0) or 0)
    entries = []
    if source.exists():
        for path in sorted(source.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.name == "README.txt":
                continue
            first_seen = path.stat().st_mtime
            offset_ms = max(0, int((first_seen - start) * 1000)) if start else 0
            entries.append((path, first_seen, offset_ms))
    rows = []
    for index, (path, first_seen, offset_ms) in enumerate(entries):
        rows.append({"source_queue_file": str(path), "source_name": path.name, "first_seen_epoch": first_seen, "first_seen_offset_ms": offset_ms, "materialized_id": f"{index:06d}"})
    fields = ["source_queue_file", "source_name", "first_seen_epoch", "first_seen_offset_ms", "materialized_id"]
    with (root / "first_seen_metadata.tsv").open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t"); writer.writeheader(); writer.writerows(rows)
    checkpoint_rows = []
    for checkpoint in checkpoints:
        cp_root = root / f"checkpoint_{checkpoint:04d}m"
        corpus = cp_root / "queue"
        corpus.mkdir(parents=True, exist_ok=True)
        selected = [row for row in rows if int(row["first_seen_offset_ms"]) <= checkpoint * 60 * 1000]
        for row in selected:
            source_path = Path(row["source_queue_file"])
            name = material_name(int(row["materialized_id"]), int(row["first_seen_offset_ms"]), row["source_name"])
            shutil.copy2(source_path, corpus / name)
        (cp_root / "selected_seeds.tsv").write_text("materialized_name\tsource_name\tfirst_seen_offset_ms\n" + "\n".join(
            f"{material_name(int(row['materialized_id']), int(row['first_seen_offset_ms']), row['source_name'])}\t{row['source_name']}\t{row['first_seen_offset_ms']}" for row in selected
        ) + ("\n" if selected else ""))
        checkpoint_rows.append({"checkpoint_min": checkpoint, "cutoff_offset_ms": checkpoint * 60 * 1000, "seed_count": len(selected), "corpus": str(corpus)})
    manifest = {
        "run_dir": str(run), "source_queue": str(source), "method": "queue_file_mtime_first_seen",
        "first_seen_source": "queue file st_mtime; initial seeds are clamped to offset 0", "start_time_epoch": start,
        "checkpoints_min": list(checkpoints), "checkpoint_rows": checkpoint_rows, "final_queue_file_count": len(rows),
        "no_final_queue_substitution": True, "replay_dbms_version": "SQLite 3.54.0",
        "replay_instrumentation": "SQLeek unified LLVM coverage", "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "checkpoint_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (root / "replay_plan.md").write_text("# SQLite checkpoint replay plan\n\n" +
        f"Run: `{run}`\n\n" +
        "Checkpoint corpora are independently materialized prefixes selected by queue-file first-seen metadata. The final 24h queue is not substituted for early checkpoints.\n\n" +
        "Replay DBMS: SQLite 3.54.0\nReplay instrumentation: SQLeek unified LLVM coverage\n" +
        "Required statistics: high-risk target-region branch coverage, target hit rate, global branch coverage, global line coverage.\n")


def wait_for_exit(run: Path, poll: int) -> None:
    while not (run / "exit_code").exists():
        time.sleep(max(5, poll))
    for _ in range(30):
        if (run / "end_manifest.json").exists():
            return
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--checkpoints", default=",".join(map(str, CHECKPOINTS)))
    args = parser.parse_args()
    run = args.run.resolve()
    if args.wait:
        wait_for_exit(run, args.poll_seconds)
    checkpoints = tuple(sorted({int(item) for item in args.checkpoints.split(",") if item.strip()}))
    info = runtime_info(run)
    collect_final(run, info, args.prepare_only)
    prepare_checkpoints(run, checkpoints)
    print(json.dumps({"run": str(run), "runtime": info, "checkpoint_manifest": str(run / "checkpoint_replay" / "checkpoint_manifest.json")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
