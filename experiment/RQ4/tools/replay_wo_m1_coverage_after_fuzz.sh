#!/usr/bin/env bash
set -euo pipefail

# Replay the completed RQ4 w/o-M1 campaigns at the paper's 1440-minute
# checkpoint.  The fuzzer queues are staged with deterministic time:<ms>
# names, while the original queues are kept untouched.

ROOT=/root/SQLeek
RQ4_ROOT="$ROOT/experiment/RQ4"
RQ2_REPLAY="$ROOT/experiment/RQ2/replay"
RQ2_SCRIPTS="$ROOT/experiment/RQ2/scripts"
LIVE_ROOT="$RQ4_ROOT/live"

MONETDB_CAMPAIGN="$LIVE_ROOT/rq4_wo_m1_monetdb_parserfix_5x24_20260802_144914"
POSTGRES_CAMPAIGN="$LIVE_ROOT/rq4_wo_m1_postgres_parallel_campaign_20260802_072900"

MONETDB_IMAGE=griffin_monetdb_llvmcov:latest
POSTGRES_IMAGE=griffin_postgres_llvmcov:latest
CHECKPOINT_MIN=1440
CHECKPOINT_MS=86400000

MONETDB_CONTAINERS=(
  sqleek_stage3_monetdb_rq4_wo_m1_monetdb_parserfix_r1_20260802_145305
  sqleek_stage3_monetdb_rq4_wo_m1_monetdb_parserfix_r2_20260802_145305
  sqleek_stage3_monetdb_rq4_wo_m1_monetdb_parserfix_r3_20260802_145305
  sqleek_stage3_monetdb_rq4_wo_m1_monetdb_parserfix_r4_20260802_145305
  sqleek_stage3_monetdb_rq4_wo_m1_monetdb_parserfix_r5_20260802_145305
)
POSTGRES_CONTAINERS=(
  sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r1_20260802_073104
  sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r2_20260802_073104
  sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r3_20260802_073104
  sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r4_20260802_073104
  sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r5_20260802_073104
)

STAMP=${RQ4_REPLAY_STAMP:-$(date -u +%Y%m%d_%H%M%S)}
OUT_ROOT="$LIVE_ROOT/rq4_wo_m1_coverage_replay_${STAMP}"
mkdir -p "$OUT_ROOT" "$OUT_ROOT/monetdb" "$OUT_ROOT/postgres" "$OUT_ROOT/logs"
exec > >(tee -a "$OUT_ROOT/replay_coordinator.log") 2>&1

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

resolve_queue() {
  local campaign="$1"
  local candidate
  for candidate in \
    "$campaign"/output/*/default/queue \
    "$campaign"/runs/*/*/output/*/default/queue; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

wait_for_fuzzers() {
  local -a containers=("${MONETDB_CONTAINERS[@]}" "${POSTGRES_CONTAINERS[@]}")
  local active=1
  local last_report=0
  local now elapsed status container
  log 'Waiting for all formal fuzzers to finish before replay.'
  while [[ "$active" -eq 1 ]]; do
    active=0
    for container in "${containers[@]}"; do
      status=$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || printf 'missing')
      case "$status" in
        running|created|restarting|paused)
          active=1
          ;;
      esac
    done
    if [[ "$active" -eq 1 ]]; then
      now=$(date +%s)
      elapsed=$((now - last_report))
      if [[ "$elapsed" -ge 600 ]]; then
        log 'At least one formal fuzzer is still active; checking again in 60 seconds.'
        last_report="$now"
      fi
      sleep 60
    fi
  done
  {
    printf 'container\tstate\texit_code\tfinished_at\n'
    for container in "${containers[@]}"; do
      docker inspect --format '{{.Name}}\t{{.State.Status}}\t{{.State.ExitCode}}\t{{.State.FinishedAt}}' "$container" 2>/dev/null \
        | sed 's#^/##' || printf '%s\tmissing\t\t\n' "$container"
    done
  } > "$OUT_ROOT/fuzzer_completion.tsv"
  log 'All formal fuzzer containers have stopped (or were already removed).'
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

run_monetdb_one() {
  local repeat="$1"
  local source_queue="${2:-$MONETDB_QUEUE}"
  local work="$OUT_ROOT/monetdb/r${repeat}"
  local staged="$work/queue_time"
  local name="sqleek_rq4_wo_m1_monetdb_replay_${STAMP}_r${repeat}"
  mkdir -p "$work"
  stage_queue "$source_queue" "$staged" "$work/queue_mapping.tsv"
  log "MonetDB replay r${repeat}: $(find "$staged" -maxdepth 1 -type f | wc -l | tr -d ' ') seeds."
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
  if [[ "$rc" -eq 0 && -s "$work/sqleek_monetdb_r${repeat}_t${CHECKPOINT_MIN}.profdata" ]]; then
    printf 'complete\n' > "$work/status"
    log "MonetDB replay r${repeat} completed."
  else
    printf 'failed\trc=%s\n' "$rc" > "$work/status"
    log "MonetDB replay r${repeat} failed (rc=${rc}); raw output was retained."
  fi
  return "$rc"
}

run_postgres_one() {
  local repeat="$1"
  local source_queue="${2:-$POSTGRES_QUEUE}"
  local work="$OUT_ROOT/postgres/r${repeat}"
  local staged="$work/queue_time"
  local name="sqleek_rq4_wo_m1_postgres_replay_${STAMP}_r${repeat}"
  local prefix="/rq2_out/postgres_sqleek_r${repeat}"
  local profdata="$work/postgres_sqleek_r${repeat}_t${CHECKPOINT_MIN}.profdata"
  local cov_json="$work/postgres_sqleek_r${repeat}_t${CHECKPOINT_MIN}.cov.json"
  local report="$work/postgres_sqleek_r${repeat}_t${CHECKPOINT_MIN}.report.txt"
  local start end rc status seed_count
  mkdir -p "$work"
  stage_queue "$source_queue" "$staged" "$work/queue_mapping.tsv"
  seed_count=$(find "$staged" -maxdepth 1 -type f | wc -l | tr -d ' ')
  log "PostgreSQL replay r${repeat}: ${seed_count} seeds."
  start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set +e
  docker run --rm --privileged --memory=64g --shm-size=8g \
    -e GRIFFIN_CONTAINER=1 \
    -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
    -e RQ2_REPLAY_SERVER_CHECK_INTERVAL=1 \
    -v "$RQ2_REPLAY/container_replay_llvm_bucketed.sh:/rq2_scripts/container_replay_llvm_bucketed.sh:ro" \
    -v "$staged:/rq2_queue:ro" \
    -v "$work:/rq2_out" \
    --name "$name" \
    --entrypoint /bin/bash "$POSTGRES_IMAGE" \
    /rq2_scripts/container_replay_llvm_bucketed.sh \
      --dbms postgres \
      --binary /root/bin_aflpp/usr/local/pgsql/bin/postgres \
      --checkpoints-ms "$CHECKPOINT_MS" \
      --seed-timeout 60 \
      --process-name pg_c_8888 \
      --out-prefix "$prefix" \
      --reset-script /workspace/scripts/reset_lv1.sh \
      --test-script /workspace/scripts/testt \
    > "$work/docker.log" 2>&1
  rc=$?
  set -e
  end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ "$rc" -eq 0 && -s "$profdata" ]]; then
    status=complete
  else
    status=failed
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "sqleek_wo_m1_postgres_r${repeat}" \
    'SQLeek-w/o-M1' postgres "$repeat" "$CHECKPOINT_MIN" "$cov_json" "$report" \
    "$status" "docker_rc=${rc}" "$POSTGRES_IMAGE" \
    /root/bin_aflpp/usr/local/pgsql/bin/postgres "$seed_count" \
    'rq4_wo_m1_formal_5x24' '' '' "$start" "$end" \
    > "$work/replay_index_row.tsv"
  if [[ "$status" == complete ]]; then
    log "PostgreSQL replay r${repeat} completed."
  else
    log "PostgreSQL replay r${repeat} failed (rc=${rc}); raw output was retained."
  fi
  return "$rc"
}

run_pair() {
  local function_name="$1"
  local first="$2"
  local second="$3"
  set +e
  "$function_name" "$first" &
  local first_pid=$!
  "$function_name" "$second" &
  local second_pid=$!
  wait "$first_pid"
  local first_rc=$?
  wait "$second_pid"
  local second_rc=$?
  set -e
  [[ "$first_rc" -eq 0 && "$second_rc" -eq 0 ]]
}

write_postgres_index() {
  local index="$OUT_ROOT/postgres/replay_index.tsv"
  printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$index"
  for repeat in 1 2 3 4 5; do
    if [[ -f "$OUT_ROOT/postgres/r${repeat}/replay_index_row.tsv" ]]; then
      cat "$OUT_ROOT/postgres/r${repeat}/replay_index_row.tsv" >> "$index"
    fi
  done
}

prepare_monetdb_install_root() {
  local install_root="$OUT_ROOT/monetdb/install_stdlibs"
  if [[ -x "$install_root/bin/mserver5" ]]; then
    printf '%s\n' "$install_root"
    return 0
  fi
  mkdir -p "$install_root"
  local container_id
  container_id=$(docker create --entrypoint /bin/true "$MONETDB_IMAGE")
  docker cp "$container_id:/monetdb_llvmcov/." "$install_root/"
  docker rm "$container_id" >/dev/null
  if [[ ! -x "$install_root/bin/mserver5" ]]; then
    echo "failed to extract /monetdb_llvmcov from $MONETDB_IMAGE" >&2
    return 1
  fi
  printf '%s\n' "$install_root"
}

write_metadata() {
  local monetdb_queue="$1"
  local postgres_queue="$2"
  {
    printf 'created_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'checkpoint_min\t%s\n' "$CHECKPOINT_MIN"
    printf 'checkpoint_ms\t%s\n' "$CHECKPOINT_MS"
    printf 'monetdb_campaign\t%s\n' "$MONETDB_CAMPAIGN"
    printf 'postgres_campaign\t%s\n' "$POSTGRES_CAMPAIGN"
    printf 'monetdb_queue\t%s\n' "$monetdb_queue"
    printf 'postgres_queue\t%s\n' "$postgres_queue"
    printf 'target_regions_monetdb\t%s\n' "$RQ4_ROOT/../RQ3/result/audit/sqleek_monetdb/target_regions.csv"
    printf 'target_regions_postgres\t%s\n' "$RQ4_ROOT/../RQ3/result/audit/sqleek_postgres/target_regions.csv"
    printf 'evaluation_rule\tfrozen RQ3 target_regions.csv; w/o-M1 drive targets are not used as denominator\n'
  } > "$OUT_ROOT/replay_metadata.tsv"
}

wait_for_fuzzers

MONETDB_QUEUE=$(resolve_queue "$MONETDB_CAMPAIGN")
POSTGRES_QUEUE=$(resolve_queue "$POSTGRES_CAMPAIGN")
write_metadata "$MONETDB_QUEUE" "$POSTGRES_QUEUE"
log "Resolved MonetDB queue: $MONETDB_QUEUE"
log "Resolved PostgreSQL queue: $POSTGRES_QUEUE"

cp -f "$ROOT/experiment/RQ3/result/audit/sqleek_monetdb/target_regions.csv" "$OUT_ROOT/target_regions_monetdb.csv"
cp -f "$ROOT/experiment/RQ3/result/audit/sqleek_postgres/target_regions.csv" "$OUT_ROOT/target_regions_postgres.csv"

docker run --rm --entrypoint /bin/bash "$MONETDB_IMAGE" -lc \
  'test -x /monetdb_llvmcov/bin/mserver5 && test -x /root/bin_original/usr/local/bin/mclient && command -v llvm-profdata-12 && command -v llvm-cov-12'
docker run --rm --entrypoint /bin/bash "$POSTGRES_IMAGE" -lc \
  'test -x /root/bin_aflpp/usr/local/pgsql/bin/postgres && test -x /workspace/scripts/reset_lv1.sh && test -x /workspace/scripts/testt && command -v llvm-profdata-12 && command -v llvm-cov-12'

log 'Starting MonetDB replay in pairs (r1+r2, r3+r4, r5).'
run_pair run_monetdb_one 1 2 || log 'MonetDB pair r1+r2 had a failure; continuing with remaining repeats.'
run_pair run_monetdb_one 3 4 || log 'MonetDB pair r3+r4 had a failure; continuing with remaining repeats.'
run_monetdb_one 5 || log 'MonetDB r5 had a failure; continuing with PostgreSQL.'

log 'Starting PostgreSQL replay in pairs (r1+r2, r3+r4, r5).'
run_pair run_postgres_one 1 2 || log 'PostgreSQL pair r1+r2 had a failure; continuing with remaining repeats.'
run_pair run_postgres_one 3 4 || log 'PostgreSQL pair r3+r4 had a failure; continuing with r5.'
run_postgres_one 5 || log 'PostgreSQL r5 had a failure; continuing to summarization.'
write_postgres_index

log 'Rebuilding PostgreSQL target-region coverage from the 1440-minute profdata.'
set +e
python3 "$RQ2_SCRIPTS/resummarize_postgres_from_profdata.py" \
  --run-dir "$OUT_ROOT/postgres" \
  --out "$OUT_ROOT/postgres/data" \
  --target-regions "$ROOT/experiment/RQ3/result/audit/sqleek_postgres/target_regions.csv" \
  --image "$POSTGRES_IMAGE" \
  --binary /root/bin_aflpp/usr/local/pgsql/bin/postgres \
  --source-root /src \
  --tool 'SQLeek-w/o-M1' \
  > "$OUT_ROOT/logs/resummarize_postgres.log" 2>&1
POSTGRES_SUMMARY_RC=$?
set -e

log 'Rebuilding MonetDB target-region coverage from the 1440-minute profdata.'
set +e
MONETDB_INSTALL_ROOT=$(prepare_monetdb_install_root)
MONETDB_INSTALL_RC=$?
if [[ "$MONETDB_INSTALL_RC" -eq 0 ]]; then
  python3 "$RQ2_SCRIPTS/resummarize_monetdb_from_profdata.py" \
    --out-dir "$OUT_ROOT/monetdb/data" \
    --target-regions "$ROOT/experiment/RQ3/result/audit/sqleek_monetdb/target_regions.csv" \
    --profdata-root "$OUT_ROOT/monetdb" \
    --allow-partial-repeats \
    --image "$MONETDB_IMAGE" \
    --binary /monetdb_llvmcov/bin/mserver5 \
    --source-root /src \
    --install-root "$MONETDB_INSTALL_ROOT" \
    --source-host "$ROOT/sources/monetdb" \
    --tool 'SQLeek-w/o-M1' \
    > "$OUT_ROOT/logs/resummarize_monetdb.log" 2>&1
  MONETDB_SUMMARY_RC=$?
else
  MONETDB_SUMMARY_RC="$MONETDB_INSTALL_RC"
fi
set -e

{
  printf 'replay_output\t%s\n' "$OUT_ROOT"
  printf 'checkpoint_min\t%s\n' "$CHECKPOINT_MIN"
  printf 'postgres_summary_rc\t%s\n' "$POSTGRES_SUMMARY_RC"
  printf 'monetdb_summary_rc\t%s\n' "$MONETDB_SUMMARY_RC"
  printf 'completed_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT_ROOT/REPLAY_SUMMARY.tsv"

if [[ "$POSTGRES_SUMMARY_RC" -ne 0 || "$MONETDB_SUMMARY_RC" -ne 0 ]]; then
  log "Replay finished with summary warnings (PostgreSQL rc=${POSTGRES_SUMMARY_RC}, MonetDB rc=${MONETDB_SUMMARY_RC}); raw profdata is retained."
  exit 1
fi
log 'Replay and both coverage summaries completed successfully.'
