#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2/replay
RUNNER=$ROOT/sqlaser_sqlite_replay/container_replay_sqlite_checkpoint_dirs.sh
IMAGE=${IMAGE:-griffin_sqlite_llvmcov}
BINARY=${BINARY:-/root/bld_llvmcov/sqlite3}
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=${OUT:-$ROOT/output/sqlaser_sqlite354_formal_replay_3532_${TS}}
CHECKPOINTS=${CHECKPOINTS:-60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${SEED_TIMEOUT:-10}
ROLLING_MERGE_INTERVAL=${ROLLING_MERGE_INTERVAL:-50}
MIN_FREE_KB=${MIN_FREE_KB:-15728640}
MEMORY=${REPLAY_MEMORY:-16g}
TMPFS_TMP=${TMPFS_TMP:-2g}
TMPFS_DB=${TMPFS_DB:-512m}
TOOL=${TOOL:-SQLaser}
VERSION=${VERSION:-fuzzing_sqlite=3.54.0,replay_sqlite=3.53.2}
BASE=/root/SQLeek/experiment/RQ2/sqlaser/results/sqlite354/formal_24h

declare -A RUN_DIRS=(
  [r1]=r1_20260711_174208
  [r2]=r2_20260711_184300
  [r3]=r3_20260711_184300
  [r4]=r4_20260711_184300
  [r5]=r5_20260711_184300
)

declare -A CPUS=(
  [r1]=44
  [r2]=45
  [r3]=44
  [r4]=45
  [r5]=44
)

mkdir -p "$OUT"/{logs,repeats,manifests}

image_id=$(docker image inspect -f '{{.Id}}' "$IMAGE")
sqlite_version=$(docker run --rm "$IMAGE" "$BINARY" --version | head -1)
start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
free_before_kb=$(df -Pk "$OUT" | awk 'NR == 2 {print $4}')

cat > "$OUT/manifests/replay_manifest.json" <<JSON
{
  "kind": "SQLaser SQLite formal LLVM replay",
  "image": "$IMAGE",
  "image_id": "$image_id",
  "binary": "$BINARY",
  "sqlite_replay_version": "$sqlite_version",
  "fuzzing_sqlite_version": "3.54.0",
  "coverage_replay_sqlite_version": "3.53.2",
  "version_mismatch_user_override": true,
  "checkpoints_min": "$CHECKPOINTS",
  "parallel_batches": [["r1","r2"], ["r3","r4"], ["r5"]],
  "seed_timeout_seconds": $SEED_TIMEOUT,
  "rolling_merge_interval": $ROLLING_MERGE_INTERVAL,
  "minimum_free_kb": $MIN_FREE_KB,
  "memory": "$MEMORY",
  "tmpfs_tmp": "$TMPFS_TMP",
  "tmpfs_db": "$TMPFS_DB",
  "start_time": "$start_time",
  "disk_free_before_kb": $free_before_kb
}
JSON

check_repeat_inputs() {
  local repeat="$1"
  local cp
  for cp in ${CHECKPOINTS//,/ }; do
    local q="$BASE/${RUN_DIRS[$repeat]}/checkpoint_replay/checkpoint_$(printf '%04d' "$cp")m/queue"
    [[ -d "$q" ]] || { echo "missing checkpoint queue: $q" >&2; exit 2; }
    local count
    count=$(find "$q" -maxdepth 1 -type f | wc -l | tr -d ' ')
    [[ "$count" -gt 0 ]] || { echo "empty checkpoint queue: $q" >&2; exit 2; }
  done
}

launch_repeat() {
  local repeat="$1"
  check_repeat_inputs "$repeat"
  local src="$BASE/${RUN_DIRS[$repeat]}/checkpoint_replay"
  local repeat_out="$OUT/repeats/$repeat"
  local run_id="sqlaser_sqlite354_${repeat}"
  mkdir -p "$repeat_out"
  local command_file="$repeat_out/command.txt"
  cat > "$command_file" <<EOF
docker run --rm --name sqlaser_sqlite_replay_${repeat}_${TS} --cpuset-cpus ${CPUS[$repeat]} --memory $MEMORY --tmpfs /tmp:rw,nosuid,nodev,size=$TMPFS_TMP --tmpfs /rq2_sqlite_tmp:rw,nosuid,nodev,size=$TMPFS_DB -e ROLLING_MERGE_INTERVAL=$ROLLING_MERGE_INTERVAL -e MIN_FREE_KB=$MIN_FREE_KB -v $RUNNER:/runner:ro -v $src:/rq2_checkpoints:ro -v $repeat_out:/rq2_out --entrypoint /bin/bash $IMAGE /runner --binary $BINARY --checkpoints-min $CHECKPOINTS --seed-timeout $SEED_TIMEOUT --out-root /rq2_out --repeat-id ${repeat#r} --run-id $run_id --tool $TOOL --version $VERSION
EOF
  docker run --rm --name "sqlaser_sqlite_replay_${repeat}_${TS}" \
    --cpuset-cpus "${CPUS[$repeat]}" --memory "$MEMORY" \
    --tmpfs /tmp:rw,nosuid,nodev,size="$TMPFS_TMP" \
    --tmpfs /rq2_sqlite_tmp:rw,nosuid,nodev,size="$TMPFS_DB" \
    -e ROLLING_MERGE_INTERVAL="$ROLLING_MERGE_INTERVAL" \
    -e MIN_FREE_KB="$MIN_FREE_KB" \
    -v "$RUNNER":/runner:ro \
    -v "$src":/rq2_checkpoints:ro \
    -v "$repeat_out":/rq2_out \
    --entrypoint /bin/bash "$IMAGE" /runner \
      --binary "$BINARY" \
      --checkpoints-min "$CHECKPOINTS" \
      --seed-timeout "$SEED_TIMEOUT" \
      --out-root /rq2_out \
      --repeat-id "${repeat#r}" \
      --run-id "$run_id" \
      --tool "$TOOL" \
      --version "$VERSION" \
    > "$repeat_out/container.stdout.log" 2> "$repeat_out/container.stderr.log"
}

run_batch() {
  local batch_name="$1"
  shift
  local repeats=("$@")
  local pids=()
  local repeat
  echo "starting_batch=$batch_name repeats=${repeats[*]} time=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT/logs/batches.log"
  for repeat in "${repeats[@]}"; do
    launch_repeat "$repeat" &
    pids+=("$!")
  done
  local rc=0
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      rc=1
    fi
  done
  echo "finished_batch=$batch_name rc=$rc time=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT/logs/batches.log"
  if [[ "$rc" -ne 0 ]]; then
    exit "$rc"
  fi
}

run_batch batch1 r1 r2
run_batch batch2 r3 r4
run_batch batch3 r5

cat "$OUT"/repeats/*/replay_index.tsv > "$OUT/replay_index.raw.tsv"
head -1 "$OUT/repeats/r1/replay_index.tsv" > "$OUT/replay_index.tsv"
grep -h -v '^run_id' "$OUT"/repeats/*/replay_index.tsv >> "$OUT/replay_index.tsv"

end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
free_after_kb=$(df -Pk "$OUT" | awk 'NR == 2 {print $4}')
du_kb=$(du -sk "$OUT" | awk '{print $1}')
cat > "$OUT/manifests/replay_summary.json" <<JSON
{
  "status": "complete",
  "start_time": "$start_time",
  "end_time": "$end_time",
  "disk_free_before_kb": $free_before_kb,
  "disk_free_after_kb": $free_after_kb,
  "output_du_kb": $du_kb,
  "replay_index": "$OUT/replay_index.tsv"
}
JSON

cat "$OUT/manifests/replay_summary.json"
