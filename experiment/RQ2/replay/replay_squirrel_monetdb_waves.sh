#!/usr/bin/env bash
set -euo pipefail

# Replay saved SQUIRREL MonetDB queues in two sequential waves.
# Original queue files are never changed: each replay input is a hard-link
# snapshot with a unique flat name for the in-container MonetDB runner.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE=/root/SQLeek/experiment/RQ2/live/monetdb_adapters/20260821_064823/squirrel_monetdb
OUT_ROOT=/root/SQLeek/experiment/RQ2/replay/output
IMAGE=griffin_monetdb_llvmcov:latest
WAVE1=r1,r2,r3
WAVE2=r4,r5
CHECKPOINTS_MIN=1440
MAX_SEEDS=0
SEED_TIMEOUT=120

usage() {
  cat <<'USAGE'
Usage: replay_squirrel_monetdb_waves.sh [options]

Options:
  --source DIR          SQUIRREL MonetDB root containing r1-r5
  --out-root DIR        Replay output root
  --image IMAGE         LLVM coverage Docker image
  --wave1 LIST          First wave repeats, default r1,r2,r3
  --wave2 LIST          Second wave repeats, default r4,r5
  --checkpoints-min L   Cumulative replay checkpoints, default 1440
  --max-seeds N         Limit each repeat for smoke tests; 0 means all
  --seed-timeout SEC    Per-seed timeout, default 120
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --wave1) WAVE1="$2"; shift 2 ;;
    --wave2) WAVE2="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --max-seeds) MAX_SEEDS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -d "$SOURCE" ]]; then
  echo "missing SQUIRREL source: $SOURCE" >&2
  exit 3
fi

TS=$(date -u +%Y%m%d_%H%M%S)
RUN_ROOT="$OUT_ROOT/squirrel_monetdb_waves_$TS"
mkdir -p "$RUN_ROOT"

printf 'metric\tvalue\n' > "$RUN_ROOT/run_meta.tsv"
printf 'source\t%s\nimage\t%s\nwave1\t%s\nwave2\t%s\ncheckpoints_min\t%s\nmax_seeds\t%s\nseed_timeout\t%s\nstarted_utc\t%s\n' \
  "$SOURCE" "$IMAGE" "$WAVE1" "$WAVE2" "$CHECKPOINTS_MIN" "$MAX_SEEDS" "$SEED_TIMEOUT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  >> "$RUN_ROOT/run_meta.tsv"
printf 'wave\trepeat\tstatus\texit_code\tqueue_files\tstarted_utc\tended_utc\toutput_dir\tlog\n' \
  > "$RUN_ROOT/replay_status.tsv"

cat > "$RUN_ROOT/README.md" <<EOF
# SQUIRREL MonetDB LLVM replay

- Image: \`$IMAGE\`
- Input source: \`$SOURCE\`
- Wave 1: \`$WAVE1\` (parallel)
- Wave 2: \`$WAVE2\` (started only after wave 1 has ended)
- Checkpoint(s): \`$CHECKPOINTS_MIN\` minute(s)
- Original queue files are read-only; replay inputs are hard-link snapshots.
- Coverage evidence is produced by LLVM \`profdata\`/\`llvm-cov\`, not AFL queue statistics.

The replay is an exploratory SQUIRREL queue replay. It does not modify the
paper result tables automatically.
EOF

preflight() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "missing Docker image: $IMAGE" >&2
    exit 4
  }
  docker run --rm --entrypoint /bin/bash "$IMAGE" -lc \
    'set -e; test -x /monetdb_llvmcov/bin/mserver5; test -x /root/bin_original/usr/local/bin/mclient; test -x /workspace/bld_griffin/autodriver_odbc_v5_aflpp; command -v llvm-profdata-12 >/dev/null; command -v llvm-cov-12 >/dev/null; strings /monetdb_llvmcov/bin/mserver5 | grep -q __llvm_prf'
}

stage_repeat() {
  local wave="$1" repeat="$2"
  local src="$SOURCE/$repeat"
  local repeat_root="$RUN_ROOT/$wave/$repeat"
  local queue_dir="$repeat_root/queue"
  local manifest="$repeat_root/queue_manifest.tsv"
  if [[ ! -d "$src" ]]; then
    echo "missing repeat source: $src" >&2
    return 1
  fi
  mkdir -p "$queue_dir"
  printf 'snapshot_index\tsource_queue_file\tsnapshot_queue_file\n' > "$manifest"
  local index=0
  local src_file snapshot_name
  while IFS= read -r -d '' src_file; do
    snapshot_name=$(printf '%08d_%s' "$index" "$(basename -- "$src_file")")
    ln -- "$src_file" "$queue_dir/$snapshot_name"
    printf '%08d\t%s\t%s\n' "$index" "$src_file" "$queue_dir/$snapshot_name" >> "$manifest"
    index=$((index + 1))
  done < <(find "$src" -type f -path '*/queue/*' ! -name .state -print0 | sort -z)
  printf 'metric\tvalue\nsource_repeat\t%s\nqueue_files\t%s\nstaged_utc\t%s\n' \
    "$src" "$index" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$repeat_root/queue_meta.tsv"
}

run_repeat() {
  local wave="$1" repeat="$2"
  local repeat_root="$RUN_ROOT/$wave/$repeat"
  local queue_dir="$repeat_root/queue"
  local out_dir="$repeat_root/output"
  local log="$repeat_root/docker.log"
  local container="squirrel_monetdb_replay_${TS}_${wave}_${repeat}"
  local started ended rc queue_files
  mkdir -p "$out_dir"
  queue_files=$(find "$queue_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set +e
  docker run --rm --privileged --shm-size=4G \
    --label sqleek.replay=squirrel-monetdb-llvm \
    --label sqleek.replay.wave="$wave" \
    --label sqleek.replay.repeat="$repeat" \
    --name "$container" \
    -e GRIFFIN_CONTAINER=1 \
    -e MSERVER_BINARY=/monetdb_llvmcov/bin/mserver5 \
    -e MCLIENT_BINARY=/root/bin_original/usr/local/bin/mclient \
    -e LLVM_PROFDATA_BIN=llvm-profdata-12 \
    -e LLVM_COV_BIN=llvm-cov-12 \
    -e RQ2_RUN_STAMP="${TS}_${wave}_${repeat}" \
    -v "$queue_dir:/rq2_queue:ro" \
    -v "$out_dir:/rq2_out" \
    -v "$SCRIPT_DIR/monetdb_bucketed_replay_runner.sh:/rq2_runner.sh:ro" \
    --entrypoint /bin/bash "$IMAGE" \
    /rq2_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "${wave}_${repeat}" \
      --checkpoints-min "$CHECKPOINTS_MIN" \
      --max-seeds "$MAX_SEEDS" \
      --seed-timeout "$SEED_TIMEOUT" \
    > "$log" 2>&1
  rc=$?
  set -e
  ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ "$rc" -eq 0 ]]; then
    printf '%s\t%s\tcomplete\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$wave" "$repeat" "$rc" "$queue_files" "$started" "$ended" "$out_dir" "$log" >> "$RUN_ROOT/replay_status.tsv"
  else
    printf '%s\t%s\tfailed\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$wave" "$repeat" "$rc" "$queue_files" "$started" "$ended" "$out_dir" "$log" >> "$RUN_ROOT/replay_status.tsv"
  fi
  return "$rc"
}

run_wave() {
  local wave="$1" repeats_csv="$2"
  local -a repeats=()
  local repeat
  IFS=',' read -r -a repeats <<< "$repeats_csv"
  mkdir -p "$RUN_ROOT/$wave"
  local -a pids=()
  local -a active_repeats=()
  for repeat in "${repeats[@]}"; do
    [[ -n "$repeat" ]] || continue
    stage_repeat "$wave" "$repeat"
    run_repeat "$wave" "$repeat" &
    pids+=("$!")
    active_repeats+=("$repeat")
  done
  local index rc failed=0
  for index in "${!pids[@]}"; do
    set +e
    wait "${pids[$index]}"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      failed=1
      echo "wave=$wave repeat=${active_repeats[$index]} failed rc=$rc; continuing wave barrier" >&2
    fi
  done
  printf 'wave\t%s\nrepeats\t%s\nfailed\t%s\nended_utc\t%s\n' \
    "$wave" "$repeats_csv" "$failed" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_ROOT/$wave/wave_status.tsv"
  return 0
}

preflight
run_wave wave1 "$WAVE1"
run_wave wave2 "$WAVE2"
printf 'ended_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_ROOT/run_meta.tsv"
echo "$RUN_ROOT"
