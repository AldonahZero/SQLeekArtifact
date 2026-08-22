#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2/replay/output/sqlaser_sqlite354_formal_replay_3532_20260713_033500
OUTBASE=/root/SQLeek/experiment/RQ2/replay/output
CLEANUP_LOG="$ROOT/manifests/cleanup_intermediates_$(date -u +%Y%m%d_%H%M%S).json"

case "$ROOT" in
  /root/SQLeek/experiment/RQ2/replay/output/sqlaser_sqlite354_formal_replay_3532_20260713_033500) ;;
  *) echo "unexpected cleanup root: $ROOT" >&2; exit 2 ;;
esac

before_kb=$(du -sk "$ROOT" | awk '{print $1}')
free_before_kb=$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')

deleted=()
delete_path() {
  local path="$1"
  if [[ -e "$path" ]]; then
    local size_kb
    size_kb=$(du -sk "$path" | awk '{print $1}')
    deleted+=("$path:$size_kb")
    rm -rf -- "$path"
  fi
}

# Formal replay intermediates. Results are retained in data/, manifests/, replay_index.host.tsv,
# logs/batches.log, and repeats/*/run_summary.json.
delete_path "$ROOT/source_map"
delete_path "$ROOT/replay_index.raw.tsv"
delete_path "$ROOT/replay_index.tsv"

for repeat_dir in "$ROOT"/repeats/r{1,2,3,4,5}; do
  [[ -d "$repeat_dir" ]] || continue
  find "$repeat_dir" -mindepth 1 -maxdepth 1 -type d -name 'checkpoint_*' -print0 | while IFS= read -r -d '' checkpoint_dir; do
    rm -rf -- "$checkpoint_dir"
  done
  rm -f -- "$repeat_dir"/command.txt
  rm -f -- "$repeat_dir"/container.stdout.log "$repeat_dir"/container.stderr.log
  rm -f -- "$repeat_dir"/leak_monitor.tsv
  rm -f -- "$repeat_dir"/replay_index.tsv "$repeat_dir"/replay_index_seeds.tsv
  rm -f -- "$repeat_dir"/seed_results.tsv
done

# Smoke and failed pre-smoke outputs are no longer needed after formal replay succeeded.
find "$OUTBASE" -maxdepth 1 -type d -name 'sqlaser_sqlite354_r1_checkpoint60_smoke_*' -print0 | while IFS= read -r -d '' smoke_dir; do
  rm -rf -- "$smoke_dir"
done
rm -f -- "$OUTBASE"/sqlaser_sqlite354_formal_replay_3532_20260713_033500.launcher.stdout
rm -f -- "$OUTBASE"/sqlaser_sqlite354_formal_replay_3532_20260713_033500.launcher.stderr

after_kb=$(du -sk "$ROOT" | awk '{print $1}')
free_after_kb=$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')

python3 - <<PY
from pathlib import Path
import json

log = Path("$CLEANUP_LOG")
data = {
    "cleanup_time_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "root": "$ROOT",
    "kept": [
        "data/",
        "manifests/",
        "logs/batches.log",
        "repeats/r*/run_summary.json",
        "replay_index.host.tsv",
        "experiment/RQ2/sqlaser/evidence/sqlite_replay_20260713_033500/",
    ],
    "removed_classes": [
        "checkpoint coverage JSON",
        "checkpoint profdata",
        "checkpoint reports/manifests",
        "per-seed replay logs",
        "source_map/sqlite3.c",
        "smoke replay output directories",
    ],
    "size_before_kb": int("$before_kb"),
    "size_after_kb": int("$after_kb"),
    "freed_kb_within_formal_root": int("$before_kb") - int("$after_kb"),
    "disk_free_before_kb": int("$free_before_kb"),
    "disk_free_after_kb": int("$free_after_kb"),
    "disk_free_delta_kb": int("$free_after_kb") - int("$free_before_kb"),
}
log.write_text(json.dumps(data, indent=2) + "\n")
print(log)
print(json.dumps(data, indent=2))
PY

du -sh "$ROOT"
df -h "$ROOT"
