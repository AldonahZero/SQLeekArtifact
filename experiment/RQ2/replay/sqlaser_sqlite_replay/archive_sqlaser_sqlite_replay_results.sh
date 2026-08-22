#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2/replay/output/sqlaser_sqlite354_formal_replay_3532_20260713_033500
EVID=/root/SQLeek/experiment/RQ2/sqlaser/evidence/sqlite_replay_20260713_033500

mkdir -p "$EVID/data" "$EVID/manifests" "$EVID/repeats"
cp -a "$ROOT/data/." "$EVID/data/"
cp -a "$ROOT/manifests/replay_manifest.json" "$ROOT/manifests/replay_summary.json" "$EVID/manifests/"
cp -a "$ROOT/replay_index.host.tsv" "$EVID/replay_index.host.tsv"
cp -a "$ROOT/logs/batches.log" "$EVID/batches.log"

for r in r1 r2 r3 r4 r5; do
  mkdir -p "$EVID/repeats/$r"
  cp -a "$ROOT/repeats/$r/run_summary.json" "$EVID/repeats/$r/"
done

python3 - <<'PY'
from __future__ import annotations

import json
from pathlib import Path

root = Path("/root/SQLeek/experiment/RQ2/replay/output/sqlaser_sqlite354_formal_replay_3532_20260713_033500")
evid = Path("/root/SQLeek/experiment/RQ2/sqlaser/evidence/sqlite_replay_20260713_033500")
rows = []
for repeat in ["r1", "r2", "r3", "r4", "r5"]:
    summary = json.loads((root / "repeats" / repeat / "run_summary.json").read_text())
    leak_rows = 0
    leak = root / "repeats" / repeat / "leak_monitor.tsv"
    if leak.exists():
        for line in leak.read_text().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 6 and (parts[4] != "0" or parts[5] != "0"):
                leak_rows += 1
    rows.append(
        [
            repeat,
            summary["seeds_executed_once"],
            summary["successful_seeds"],
            summary["failed_seeds"],
            summary["timed_out_seeds"],
            summary["profraw_files_merged"],
            summary["forced_leak_cleanups"],
            summary["final_tmp_files"],
            leak_rows,
        ]
    )

with (evid / "repeat_replay_summary.csv").open("w") as fp:
    fp.write(
        "repeat,seeds_executed_once,successful_seeds,failed_seeds,timed_out_seeds,"
        "profraw_files_merged,forced_leak_cleanups,final_tmp_files,nonzero_db_leak_rows\n"
    )
    for row in rows:
        fp.write(",".join(map(str, row)) + "\n")
PY

find "$EVID" -type f -printf '%s %p\n' | sort -nr | head -20
du -sh "$EVID"
