#!/usr/bin/env bash
set -euo pipefail

# Replay the five MonetDB queues from the early-exit RQ4 w/o-M1 campaign.
# This intentionally produces a partial/early-exit result and never touches
# the original fuzzing queues.

ROOT=/root/SQLeek
RQ4_ROOT="$ROOT/experiment/RQ4"
RQ2_REPLAY="$ROOT/experiment/RQ2/replay"
LIVE_ROOT="$RQ4_ROOT/live"
CAMPAIGN="$LIVE_ROOT/rq4_wo_m1_monetdb_parserfix_5x24_20260802_144914"
MONETDB_IMAGE=griffin_monetdb_llvmcov:latest
CHECKPOINT_MIN=1440
CHECKPOINT_MS=86400000
TARGET_REGIONS="$ROOT/experiment/RQ3/result/audit/sqleek_monetdb/target_regions.csv"

STAMP=${RQ4_MONETDB_PARTIAL_STAMP:-$(date -u +%Y%m%d_%H%M%S)}
OUT_ROOT="$LIVE_ROOT/rq4_wo_m1_monetdb_partial_coverage_replay_${STAMP}"
mkdir -p "$OUT_ROOT/monetdb" "$OUT_ROOT/logs"
exec > >(tee -a "$OUT_ROOT/replay_coordinator.log") 2>&1

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

stage_queue() {
  local source_queue="$1"
  local staged_queue="$2"
  local mapping="$3"
  mkdir -p "$staged_queue"
  python3 - "$source_queue" "$staged_queue" "$mapping" <<'PY'
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
staged = Path(sys.argv[2])
mapping = Path(sys.argv[3])
files = sorted(
    (p for p in source.iterdir() if p.is_file()),
    key=lambda p: (p.stat().st_mtime_ns, p.name),
)
if not files:
    raise SystemExit(f"empty queue: {source}")

base_ns = files[0].stat().st_mtime_ns
last_ns = files[-1].stat().st_mtime_ns
span_ns = max(0, last_ns - base_ns)
rows = []
for index, source_file in enumerate(files):
    if span_ns:
        elapsed_ms = round((source_file.stat().st_mtime_ns - base_ns) * 86400000 / span_ns)
    else:
        elapsed_ms = 0
    elapsed_ms = max(0, min(86400000, int(elapsed_ms)))
    safe_name = re.sub(r"[^A-Za-z0-9._+=-]+", "_", source_file.name)
    target_name = f"seed_{index:08d}_{safe_name},time:{elapsed_ms}"
    target = staged / target_name
    try:
        os.link(source_file, target)
        mode = "hardlink"
    except OSError:
        shutil.copy2(source_file, target)
        mode = "copy"
    rows.append((index, elapsed_ms, source_file.name, target.name, mode))

with mapping.open("w", encoding="utf-8") as fp:
    fp.write("index\telapsed_ms\toriginal_name\tstaged_name\tmode\n")
    for row in rows:
        fp.write("\t".join(map(str, row)) + "\n")

print(f"staged={len(rows)} source={source} target={staged} span_ms={span_ns / 1_000_000:.3f}")
PY
}

run_one() {
  local repeat="$1"
  local source_queue="$CAMPAIGN/runs/monetdb/rq4_wo_m1_monetdb_parserfix_r${repeat}_20260802_145305/output/monetdb_memory/default/queue"
  local work="$OUT_ROOT/monetdb/r${repeat}"
  local staged="$work/queue_time"
  local name="sqleek_rq4_wo_m1_monetdb_partial_replay_${STAMP}_r${repeat}"
  mkdir -p "$work"
  stage_queue "$source_queue" "$staged" "$work/queue_mapping.tsv"
  local seed_count
  seed_count=$(find "$staged" -maxdepth 1 -type f | wc -l | tr -d ' ')
  printf '%s\n' "$seed_count" > "$work/seed_count.txt"
  log "MonetDB partial replay r${repeat}: ${seed_count} seeds."
  set +e
  docker run --rm --cpus=2 --memory=16g \
    -v "$staged:/rq2_queue:ro" \
    -v "$work:/rq2_out" \
    -v "$RQ2_REPLAY/monetdb_bucketed_replay_runner.sh:/workspace/monetdb_bucketed_replay_runner.sh:ro" \
    --name "$name" \
    --entrypoint /bin/bash "$MONETDB_IMAGE" \
    /workspace/monetdb_bucketed_replay_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "sqleek_monetdb_r${repeat}" \
      --checkpoints-min "$CHECKPOINT_MIN" \
      --seed-timeout 120 \
      --restart-every 100 \
      --port "$((50140 + repeat))" \
    > "$work/docker.log" 2>&1
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 && -s "$work/sqleek_monetdb_partial_r${repeat}_t${CHECKPOINT_MIN}.profdata" ]]; then
    printf 'complete\n' > "$work/status"
    log "MonetDB partial replay r${repeat} completed."
    return 0
  fi
  printf 'failed\trc=%s\n' "$rc" > "$work/status"
  log "MonetDB partial replay r${repeat} failed (rc=${rc}); raw output retained."
  return 1
}

run_pair() {
  local first="$1"
  local second="$2"
  set +e
  run_one "$first" &
  local first_pid=$!
  run_one "$second" &
  local second_pid=$!
  wait "$first_pid"
  local first_rc=$?
  wait "$second_pid"
  local second_rc=$?
  set -e
  [[ "$first_rc" -eq 0 && "$second_rc" -eq 0 ]]
}

prepare_monetdb_install_root() {
  local install_root="$OUT_ROOT/install_stdlibs"
  if [[ -x "$install_root/bin/mserver5" ]]; then
    printf '%s\n' "$install_root"
    return 0
  fi
  mkdir -p "$install_root"
  local container_id
  container_id=$(docker create --entrypoint /bin/true "$MONETDB_IMAGE")
  docker cp "$container_id:/monetdb_llvmcov/." "$install_root/"
  docker rm "$container_id" >/dev/null
  [[ -x "$install_root/bin/mserver5" ]] || return 1
  printf '%s\n' "$install_root"
}

log "Starting partial MonetDB replay from $CAMPAIGN"
log "Output: $OUT_ROOT"
docker run --rm --entrypoint /bin/bash "$MONETDB_IMAGE" -lc \
  'test -x /monetdb_llvmcov/bin/mserver5 && test -x /root/bin_original/usr/local/bin/mclient && command -v llvm-profdata-12 && command -v llvm-cov-12'

run_pair 1 2 || log 'MonetDB partial replay pair r1+r2 had a failure; continuing.'
run_pair 3 4 || log 'MonetDB partial replay pair r3+r4 had a failure; continuing.'
run_one 5 || log 'MonetDB partial replay r5 had a failure; continuing to summarization.'

set +e
MONETDB_INSTALL_ROOT=$(prepare_monetdb_install_root)
install_rc=$?
summary_rc=$install_rc
if [[ "$install_rc" -eq 0 ]]; then
  python3 "$ROOT/experiment/RQ2/scripts/resummarize_monetdb_from_profdata.py" \
    --out-dir "$OUT_ROOT/data" \
    --target-regions "$TARGET_REGIONS" \
    --profdata-root "$OUT_ROOT/monetdb" \
    --allow-partial-repeats \
    --image "$MONETDB_IMAGE" \
    --binary /monetdb_llvmcov/bin/mserver5 \
    --source-root /src \
    --install-root "$MONETDB_INSTALL_ROOT" \
    --source-host "$ROOT/sources/monetdb" \
    --tool 'SQLeek-W/O-M1-partial' \
    > "$OUT_ROOT/logs/resummarize_monetdb.log" 2>&1
  summary_rc=$?
fi
set -e

{
  printf 'campaign\t%s\n' "$CAMPAIGN"
  printf 'variant\tRQ4 w/o M1\n'
  printf 'status\tpartial_early_exit\n'
  printf 'checkpoint_min\t%s\n' "$CHECKPOINT_MIN"
  printf 'summary_rc\t%s\n' "$summary_rc"
  printf 'completed_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT_ROOT/REPLAY_SUMMARY.tsv"

if [[ "$summary_rc" -eq 0 ]]; then
  log 'Partial MonetDB replay and summary completed successfully.'
else
  log "Partial MonetDB replay finished with summary rc=${summary_rc}; raw profdata retained."
fi
