#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2
SOURCE_STAGE="${SQLASER_SOURCE_STAGE:-$ROOT/collected/sqlaser_formal_24h_20260821_ssh_docker_replay_inputs_20260822/monetdb}"
OUTPUT_ROOT="${SQLASER_REPLAY_OUTPUT_ROOT:-$ROOT/replay/output}"
CHECKPOINTS_MIN="${SQLASER_CHECKPOINTS_MIN:-60,180,300,480,600,720,900,1200,1440}"
IMAGE="${SQLASER_REPLAY_IMAGE:-griffin_monetdb_llvmcov:latest}"
RUN_STAMP="${SQLASER_REPLAY_STAMP:-$(date -u +%Y%m%d_%H%M%S)}"
RUN_ID="sqlaser_monetdb_llvm_replay_${RUN_STAMP}"
RUN_ROOT="$OUTPUT_ROOT/$RUN_ID"
RUNNER="$ROOT/replay/monetdb_bucketed_replay_runner.sh"
TARGET_REGIONS_SOURCE="$ROOT/replay/output/squirrel_monetdb_waves_20260822_111253/target_regions.csv"

test -d "$SOURCE_STAGE"
test -f "$RUNNER"
test -f "$TARGET_REGIONS_SOURCE"
docker image inspect "$IMAGE" >/dev/null

mkdir -p "$RUN_ROOT/data"
cp -a "$TARGET_REGIONS_SOURCE" "$RUN_ROOT/target_regions.csv"
printf 'metric\tvalue\n' > "$RUN_ROOT/run_meta.tsv"
printf 'source_stage\t%s\n' "$SOURCE_STAGE" >> "$RUN_ROOT/run_meta.tsv"
printf 'tool\tSQLaser\n' >> "$RUN_ROOT/run_meta.tsv"
printf 'dbms\tMonetDB\n' >> "$RUN_ROOT/run_meta.tsv"
printf 'image\t%s\n' "$IMAGE" >> "$RUN_ROOT/run_meta.tsv"
printf 'checkpoints_min\t%s\n' "$CHECKPOINTS_MIN" >> "$RUN_ROOT/run_meta.tsv"
printf 'started_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_ROOT/run_meta.tsv"
printf 'run\tcontainer\tcontainer_id\tstatus\texit_code\tstarted_utc\tended_utc\toutput_dir\n' > "$RUN_ROOT/replay_status.tsv"

declare -A CONTAINER_ID
declare -A STARTED_AT
for repeat in r1 r2 r3 r4 r5; do
  queue="$SOURCE_STAGE/$repeat/queue"
  out="$RUN_ROOT/$repeat"
  test -d "$queue"
  mkdir -p "$out"
  queue_count=$(find "$queue" -maxdepth 1 -type f | wc -l)
  test "$queue_count" -gt 0
  name="${RUN_ID}_${repeat}"
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  cid=$(docker run -d --rm --name "$name" \
    -v "$queue:/rq2_queue:ro" \
    -v "$out:/rq2_out" \
    -v "$RUNNER:/rq2_runner.sh:ro" \
    "$IMAGE" /bin/bash /rq2_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "$repeat" \
      --checkpoints-min "$CHECKPOINTS_MIN" \
      --max-seeds 0 \
      --seed-timeout 120)
  CONTAINER_ID[$repeat]="$cid"
  STARTED_AT[$repeat]="$started"
  printf '%s\t%s\t%s\trunning\t\t%s\t\t%s\n' \
    "$repeat" "$name" "$cid" "$started" "$out" >> "$RUN_ROOT/replay_status.tsv"
done

failed=0
for repeat in r1 r2 r3 r4 r5; do
  cid="${CONTAINER_ID[$repeat]}"
  name="${RUN_ID}_${repeat}"
  rc=$(docker wait "$cid") || rc=125
  ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  status=exited
  [[ "$rc" == 0 ]] || failed=1
  python3 - "$RUN_ROOT/replay_status.tsv" "$repeat" "$name" "$cid" "$status" "$rc" "${STARTED_AT[$repeat]}" "$ended" "$RUN_ROOT/$repeat" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
replacement = "\t".join(sys.argv[2:]) + "\n"
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
for i, line in enumerate(lines):
    if line.startswith(key + "\t"):
        lines[i] = replacement
        break
else:
    raise SystemExit(f"missing replay status row: {key}")
path.write_text("".join(lines), encoding="utf-8")
PY
done

if [[ "$failed" -ne 0 ]]; then
  printf 'status\tfailed\n' >> "$RUN_ROOT/run_meta.tsv"
  exit 1
fi

for repeat in r1 r2 r3 r4 r5; do
  count=$(find "$RUN_ROOT/$repeat" -maxdepth 1 -type f -name "${repeat}_t*.profdata" | wc -l)
  test "$count" -eq 9
  mkdir -p "$RUN_ROOT/profdata_for_resummarize/$repeat"
  for profdata in "$RUN_ROOT/$repeat"/${repeat}_t*.profdata; do
    alias="$RUN_ROOT/profdata_for_resummarize/$repeat/griffin_monetdb_${repeat}_$(basename "$profdata" | sed "s/^${repeat}_//")"
    ln "$profdata" "$alias"
  done
done

printf 'ended_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_ROOT/run_meta.tsv"
printf 'status\tall_5_replay_containers_exit0\n' >> "$RUN_ROOT/run_meta.tsv"
printf '%s\n' "$RUN_ROOT"
