#!/usr/bin/env python3
"""Collect one MySQL SQLaser prototype run without rewriting its queue."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_stats(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return result


def parse_epoch(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def plot_last(path: Path) -> dict[str, str]:
    try:
        lines = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]
    except FileNotFoundError:
        return {}
    if len(lines) < 2:
        return {}
    header = [item.strip() for item in lines[0].split(",")]
    values = [item.strip() for item in lines[-1].split(",")]
    return dict(zip(header, values))


def integer(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value or ""))
    except ValueError:
        return default


def energy(progress: int, chain_len: int, distance: int, weight: int) -> int:
    if not chain_len:
        return 1
    rounds = 4 if distance == 0 else 3 if distance == 1 else 2 if distance == 2 or progress else 1
    return min(6, rounds * max(1, weight))


def distributions(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    distances: dict[str, int] = {}
    energies = {str(index): 0 for index in range(1, 7)}
    if not path.exists():
        return distances, energies
    with path.open(errors="replace", newline="") as fp:
        for row in csv.DictReader(fp, delimiter="\t"):
            try:
                distance = integer(row.get("distance"), -1)
                progress = integer(row.get("progress"))
                chain_len = integer(row.get("chain_len"))
                weight = max(1, integer(row.get("weight"), 1))
            except (TypeError, ValueError):
                continue
            if distance < 0:
                continue
            distances[str(distance)] = distances.get(str(distance), 0) + 1
            recorded = integer(row.get("energy"))
            mapped = recorded if 1 <= recorded <= 6 else energy(progress, chain_len, distance, weight)
            energies[str(mapped)] += 1
    return distances, energies


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.iterdir() if item.is_file() and item.name != "README.txt")


def collect(run: Path) -> dict:
    output = run / "outputs" / "outputs_0"
    stats_path = output / "fuzzer_stats"
    stats = read_stats(stats_path)
    plot = plot_last(output / "plot_data")
    manifest = read_json(run / "run_manifest.json")
    end_manifest = read_json(run / "end_manifest.json")

    start_epoch = float(stats.get("start_time", 0) or 0)
    end_epoch = parse_epoch(str(end_manifest.get("end_time", "")))
    if end_epoch is None:
        end_epoch = float(stats.get("last_update", 0) or time.time())
    requested = integer(str(manifest.get("requested_runtime_seconds", 0)))
    runtime = max(0.0, end_epoch - start_epoch) if start_epoch else 0.0

    metadata = output / "sqlaser_seed_metadata.tsv"
    distances, energies = distributions(metadata)
    container_log = run / "container.log"
    log_text = container_log.read_text(errors="replace") if container_log.exists() else ""
    connection_failures = len(re.findall(r"Connection error|Can't connect|connection refused", log_text, re.I))
    mysqld_restarts = len(re.findall(r"MYSQL shutdown completed|Running SHUTDOWN_COMMAND", log_text))
    syntax_lines = len(re.findall(r"syntax error|parse error", log_text, re.I))

    custom = {
        key: plot.get(key, "0")
        for key in (
            "sqlaser_enabled", "sqlaser_target_count", "sqlaser_best_progress",
            "sqlaser_best_distance", "sqlaser_total_scored", "sqlaser_progress_seeds",
            "sqlaser_chain_hits", "sqlaser_energy_boosts",
            "sqlaser_energy_1", "sqlaser_energy_2", "sqlaser_energy_3",
            "sqlaser_energy_4", "sqlaser_energy_5", "sqlaser_energy_6",
        )
    }
    summary = {
        "run_dir": str(run),
        "mode": manifest.get("mode"),
        "mysql_version": manifest.get("mysql_version"),
        "mysql_commit": manifest.get("mysql_commit"),
        "sqlright_commit": manifest.get("sqlright_commit"),
        "sqlaser_commit": manifest.get("sqlaser_commit"),
        "cpu_core": manifest.get("cpu_core"),
        "runtime_seconds": runtime,
        "requested_runtime_seconds": requested,
        "runtime_qualified": bool(requested and runtime >= requested - 60),
        "execs_done": integer(stats.get("execs_done")),
        "execs_per_sec": stats.get("execs_per_sec", plot.get("execs_per_sec", "0")),
        "paths_total": integer(stats.get("paths_total", plot.get("paths_total"))),
        "bitmap_cvg": stats.get("bitmap_cvg", plot.get("map_size", "0")),
        "queue_files": count_files(output / "queue"),
        "crash_files": count_files(output / "crashes"),
        "hang_files": count_files(output / "hangs"),
        "unique_crashes": integer(stats.get("unique_crashes", plot.get("unique_crashes"))),
        "unique_hangs": integer(stats.get("unique_hangs", plot.get("unique_hangs"))),
        "oracle_mutations": integer(plot.get("total_mutate_num"), integer(plot.get("num_mutate"))),
        "oracle_valid_ratio": plot.get("total_random_VALID", "0"),
        "execution_ok": integer(plot.get("postgre_execute_ok")),
        "execution_failures": integer(plot.get("postgre_execute_error")),
        "execution_total": integer(plot.get("postgre_execute_total")),
        "parse_attempts": integer(plot.get("num_parse")),
        "parse_failures_from_log": syntax_lines,
        "connection_failures_from_container_log": connection_failures,
        "mysqld_restarts_from_container_log": mysqld_restarts,
        "sqlaser": custom,
        "distance_distribution": distances,
        "energy_distribution": energies,
        "artifact_paths": {
            "fuzzer_stats": str(stats_path),
            "plot_data": str(output / "plot_data"),
            "queue": str(output / "queue"),
            "bugs": str(run / "bugs"),
            "hangs": str(output / "hangs"),
            "crashes": str(output / "crashes"),
            "sqlaser_seed_metadata": str(metadata),
            "container_log": str(container_log),
        },
        "notes": [
            "distance_type=sql_structure_proxy",
            "parse_failures_from_log is a lower-bound log-derived count; SQLRight num_parse is reported separately as parse_attempts",
            "crash files are candidates and are not confirmed bugs",
        ],
    }

    final = run / "final"
    final.mkdir(parents=True, exist_ok=True)
    for name in ("fuzzer_stats", "plot_data", "sqlaser_seed_metadata.tsv"):
        source = output / name
        if source.is_file():
            shutil.copy2(source, final / name)
    (run / "distance_distribution.json").write_text(json.dumps(distances, indent=2, sort_keys=True) + "\n")
    (run / "energy_distribution.json").write_text(json.dumps(energies, indent=2, sort_keys=True) + "\n")
    (run / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (final / "distance_distribution.json").write_text(json.dumps(distances, indent=2, sort_keys=True) + "\n")
    (final / "energy_distribution.json").write_text(json.dumps(energies, indent=2, sort_keys=True) + "\n")
    (final / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (run / "collection_manifest.json").write_text(json.dumps({
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run),
        "final_dir": str(final),
        "queue_is_final_run_output": True,
        "summary": str(run / "summary.json"),
    }, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    collect(args.run.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
