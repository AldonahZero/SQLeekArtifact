#!/usr/bin/env bash
set -euo pipefail

# Replay the valid PostgreSQL RQ4 w/o-M1 repeats.  Repeat 3 is intentionally
# excluded: its fuzzing process stopped early after inode exhaustion.
# Original queues are never renamed, touched, or mtime-reset.

ROOT=/root/SQLeek
RQ4_ROOT="$ROOT/experiment/RQ4"
RQ2_REPLAY="$ROOT/experiment/RQ2/replay"
RQ2_SCRIPTS="$ROOT/experiment/RQ2/scripts"
CAMPAIGN="$RQ4_ROOT/live/rq4_wo_m1_postgres_parallel_campaign_20260802_072900"
IMAGE=griffin_postgres_llvmcov:latest

CHECKPOINT_MINUTES=(60 180 300 480 600 720 900 1200 1440)
CHECKPOINTS_MS=3600000,10800000,18000000,28800000,36000000,43200000,54000000,72000000,86400000
VALID_REPEATS=(1 2 4 5)
EXCLUDED_REPEAT=3

STAMP=${RQ4_PG_REPLAY_STAMP:-$(date -u +%Y%m%d_%H%M%S)}
OUT_ROOT="$RQ4_ROOT/live/rq4_wo_m1_postgres_coverage_replay_${STAMP}"
mkdir -p "$OUT_ROOT/postgres" "$OUT_ROOT/logs"
exec > >(tee -a "$OUT_ROOT/replay_coordinator.log") 2>&1

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

resolve_repeat_queue() {
  local repeat="$1"
  local candidate
  for candidate in "$CAMPAIGN"/runs/postgresql/*r${repeat}_*/output/postgres_memory/default/queue; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

wait_for_pg_containers() {
  local -a containers=(
    sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r1_20260802_073104
    sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r2_20260802_073104
    sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r4_20260802_073104
    sqleek_stage3_postgresql_rq4_wo_m1_postgres_parallel_r5_20260802_073104
  )
  local active=1 status container
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
      log 'A valid PostgreSQL fuzz container is still active; waiting 30 seconds.'
      sleep 30
    fi
  done
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
span_ns = max(0, files[-1].stat().st_mtime_ns - base_ns)
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

run_postgres_one() {
  local repeat="$1"
  local source_queue="${QUEUES[$repeat]}"
  local work="$OUT_ROOT/postgres/r${repeat}"
  local staged="$work/queue_time"
  local name="sqleek_rq4_wo_m1_postgres_replay_${STAMP}_r${repeat}"
  local prefix="/rq2_out/postgres_sqleek_wo_m1_r${repeat}"
  local final_profdata="$work/postgres_sqleek_wo_m1_r${repeat}_t1440.profdata"
  local final_cov="$work/postgres_sqleek_wo_m1_r${repeat}_t1440.cov.json"
  local final_report="$work/postgres_sqleek_wo_m1_r${repeat}_t1440.report.txt"
  local start end rc status seed_count

  mkdir -p "$work"
  stage_queue "$source_queue" "$staged" "$work/queue_mapping.tsv"
  seed_count=$(find "$staged" -maxdepth 1 -type f | wc -l | tr -d ' ')
  log "PostgreSQL replay r${repeat}: ${seed_count} seeds, checkpoints=${CHECKPOINTS_MS}."
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
    --entrypoint /bin/bash "$IMAGE" \
    /rq2_scripts/container_replay_llvm_bucketed.sh \
      --dbms postgres \
      --binary /root/bin_aflpp/usr/local/pgsql/bin/postgres \
      --checkpoints-ms "$CHECKPOINTS_MS" \
      --seed-timeout 60 \
      --process-name pg_c_8888 \
      --out-prefix "$prefix" \
      --reset-script /workspace/scripts/reset_lv1.sh \
      --test-script /workspace/scripts/testt \
    > "$work/docker.log" 2>&1
  rc=$?
  set -e
  end=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  if [[ "$rc" -eq 0 && -s "$final_profdata" ]]; then
    status=complete
  else
    status=failed
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "sqleek_wo_m1_postgres_r${repeat}" \
    'SQLeek-W/O-M1' postgres "$repeat" 1440 "$final_cov" "$final_report" \
    "$status" "docker_rc=${rc}" "$IMAGE" \
    /root/bin_aflpp/usr/local/pgsql/bin/postgres "$seed_count" \
    'rq4_wo_m1_formal_5x24' '' '' "$start" "$end" \
  > "$work/replay_index_row.tsv"

  : > "$work/replay_index_row.tsv"
  for checkpoint in "${CHECKPOINT_MINUTES[@]}"; do
    checkpoint_profdata="$work/postgres_sqleek_wo_m1_r${repeat}_t${checkpoint}.profdata"
    checkpoint_cov="$work/postgres_sqleek_wo_m1_r${repeat}_t${checkpoint}.cov.json"
    checkpoint_report="$work/postgres_sqleek_wo_m1_r${repeat}_t${checkpoint}.report.txt"
    checkpoint_status=failed
    if [[ "$rc" -eq 0 && -s "$checkpoint_profdata" ]]; then
      checkpoint_status=complete
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "sqleek_wo_m1_postgres_r${repeat}" \
      'SQLeek-W/O-M1' postgres "$repeat" "$checkpoint" "$checkpoint_cov" "$checkpoint_report" \
      "$checkpoint_status" "docker_rc=${rc}" "$IMAGE" \
      /root/bin_aflpp/usr/local/pgsql/bin/postgres "$seed_count" \
      'rq4_wo_m1_formal_5x24' '' '' "$start" "$end" \
      >> "$work/replay_index_row.tsv"
  done

  if [[ "$status" == complete ]]; then
    log "PostgreSQL replay r${repeat} completed."
  else
    log "PostgreSQL replay r${repeat} failed (rc=${rc}); raw output was retained."
  fi
  return "$rc"
}

run_pair() {
  local first="$1"
  local second="$2"
  set +e
  run_postgres_one "$first" &
  local first_pid=$!
  run_postgres_one "$second" &
  local second_pid=$!
  wait "$first_pid"
  local first_rc=$?
  wait "$second_pid"
  local second_rc=$?
  set -e
  [[ "$first_rc" -eq 0 && "$second_rc" -eq 0 ]]
}

write_index() {
  local index="$OUT_ROOT/postgres/replay_index.tsv"
  printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$index"
  for repeat in "${VALID_REPEATS[@]}"; do
    if [[ -f "$OUT_ROOT/postgres/r${repeat}/replay_index_row.tsv" ]]; then
      cat "$OUT_ROOT/postgres/r${repeat}/replay_index_row.tsv" >> "$index"
    fi
  done
}

wait_for_pg_containers

declare -A QUEUES
for repeat in "${VALID_REPEATS[@]}"; do
  QUEUES[$repeat]=$(resolve_repeat_queue "$repeat")
done

{
  printf 'created_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'campaign\t%s\n' "$CAMPAIGN"
  printf 'checkpoint_minutes\t%s\n' "${CHECKPOINT_MINUTES[*]}"
  printf 'valid_repeats\t%s\n' "${VALID_REPEATS[*]}"
  printf 'excluded_repeat\tr3: early inode-exhaustion failure; not a 24h repeat\n'
  for repeat in "${VALID_REPEATS[@]}"; do
    printf 'queue_r%s\t%s\n' "$repeat" "${QUEUES[$repeat]}"
  done
} > "$OUT_ROOT/replay_metadata.tsv"

cp -f "$ROOT/experiment/RQ3/result/audit/sqleek_postgres/target_regions.csv" "$OUT_ROOT/target_regions.csv"

docker run --rm --entrypoint /bin/bash "$IMAGE" -lc \
  'test -x /root/bin_aflpp/usr/local/pgsql/bin/postgres && test -x /workspace/scripts/reset_lv1.sh && test -x /workspace/scripts/testt && command -v llvm-profdata-12 && command -v llvm-cov-12'

log 'Starting PostgreSQL replay pair r1+r2.'
run_pair 1 2 || log 'Replay pair r1+r2 had a failure; continuing.'
log 'Starting PostgreSQL replay pair r4+r5.'
run_pair 4 5 || log 'Replay pair r4+r5 had a failure; continuing.'
write_index

log 'Rebuilding PostgreSQL coverage CSVs from retained LLVM profdata.'
set +e
python3 "$RQ2_SCRIPTS/resummarize_postgres_from_profdata.py" \
  --run-dir "$OUT_ROOT/postgres" \
  --out "$OUT_ROOT/data" \
  --target-regions "$ROOT/experiment/RQ3/result/audit/sqleek_postgres/target_regions.csv" \
  --image "$IMAGE" \
  --binary /root/bin_aflpp/usr/local/pgsql/bin/postgres \
  --source-root /root/postgres \
  --tool 'SQLeek-W/O-M1' \
  > "$OUT_ROOT/logs/resummarize.log" 2>&1
SUMMARY_RC=$?
set -e

{
  printf 'replay_output\t%s\n' "$OUT_ROOT"
  printf 'checkpoint_minutes\t%s\n' "${CHECKPOINT_MINUTES[*]}"
  printf 'valid_repeats\t%s\n' "${VALID_REPEATS[*]}"
  printf 'excluded_repeat\tr3\n'
  printf 'summary_rc\t%s\n' "$SUMMARY_RC"
  printf 'completed_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT_ROOT/REPLAY_SUMMARY.tsv"

if [[ "$SUMMARY_RC" -ne 0 ]]; then
  log "PostgreSQL replay summary failed (rc=${SUMMARY_RC}); raw profdata is retained."
  exit 1
fi
log 'PostgreSQL replay completed and coverage CSVs were generated.'
