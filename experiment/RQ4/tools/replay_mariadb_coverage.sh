#!/usr/bin/env bash
set -euo pipefail

# RQ4 w/o-M1 coverage replay for the five completed MariaDB campaigns.
# The fuzzing queue names do not contain AFL time fields, so this launcher
# stages hardlinks with a normalized mtime-derived `time:` field and then uses
# the existing LLVM bucketed replay runner.

REPLAY_DIR=/root/SQLeek/experiment/RQ2/replay
CAMPAIGN=/root/SQLeek/experiment/RQ4/live/rq4_wo_m1_mariadb_campaign_20260731_145943
OUT_ROOT=/root/SQLeek/experiment/RQ4/live
IMAGE=${RQ4_MARIADB_COV_IMAGE:-griffin_mariadb_llvmcov:latest}
BINARY=${RQ4_MARIADB_COV_BINARY:-/usr/local/mysql/bin/mariadbd}
RESET_SCRIPT=${RQ4_MARIADB_COV_RESET_SCRIPT:-/workspace/scripts/reset_lv1.sh}
TEST_SCRIPT_HOST=${RQ4_MARIADB_COV_TEST_SCRIPT_HOST:-/root/SQLeek/experiment/RQ4/tools/testt_mariadb_coverage.sh}
TEST_SCRIPT_CONTAINER=/rq4_scripts/testt_mariadb_coverage.sh
CHECKPOINTS_MIN=${RQ4_COV_CHECKPOINTS_MIN:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${RQ4_COV_SEED_TIMEOUT:-60}
DOCKER_MEM=${RQ4_COV_DOCKER_MEM:-16G}
DOCKER_SHM=${RQ4_COV_DOCKER_SHM:-4G}
SERVER_CHECK_INTERVAL=${RQ4_COV_SERVER_CHECK_INTERVAL:-1}
SMOKE_SEEDS=0
OUT=

RUN_IDS=(
  rq4_wo_m1_mariadb_r1_20260731_150100
  rq4_wo_m1_mariadb_r2_20260731_150442
  rq4_wo_m1_mariadb_r3_20260731_150443
  rq4_wo_m1_mariadb_r4_20260731_150444
  rq4_wo_m1_mariadb_r5_20260731_150444
)

usage() {
  cat <<'USAGE'
Usage: replay_mariadb_coverage.sh [options]

Options:
  --out DIR              Output directory; default is a timestamped RQ4 live dir
  --smoke-seeds N        Replay only the first N seeds from r1, then exit
  --checkpoints-min LIST Cumulative checkpoints, default 0,60,180,300,480,600,720,900,1200,1440
  --seed-timeout SEC     Per-seed timeout, default 60
  --docker-mem SIZE      Memory limit per replay container, default 16G
  --docker-shm SIZE      Shared memory per replay container, default 4G
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --smoke-seeds) SMOKE_SEEDS="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --docker-mem) DOCKER_MEM="$2"; shift 2 ;;
    --docker-shm) DOCKER_SHM="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$OUT" ]]; then
  OUT="$OUT_ROOT/rq4_wo_m1_mariadb_coverage_replay_$(date -u +%Y%m%d_%H%M%S)"
fi

if ! [[ "$SMOKE_SEEDS" =~ ^[0-9]+$ ]]; then
  echo "--smoke-seeds must be a non-negative integer" >&2
  exit 2
fi

mkdir -p "$OUT"/{work,logs,data,profdata}
TARGET_REGIONS=/root/SQLeek/experiment/RQ3/result/audit/sqleek_mariadb/target_regions.csv
cp "$TARGET_REGIONS" "$OUT/target_regions.csv"
sha256sum "$OUT/target_regions.csv" > "$OUT/target_regions.sha256"

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MIN"
CHECKPOINTS_MS=()
for cp in "${CHECKPOINT_ARR[@]}"; do
  if ! [[ "$cp" =~ ^[0-9]+$ ]]; then
    echo "invalid checkpoint: $cp" >&2
    exit 2
  fi
  CHECKPOINTS_MS+=("$((cp * 60 * 1000))")
done
CHECKPOINTS_MS_CSV=$(IFS=','; echo "${CHECKPOINTS_MS[*]}")

printf 'dbms\timage\tbinary\tstatus\tmessage\n' > "$OUT/preflight_status.tsv"
if docker run --rm --entrypoint /bin/bash "$IMAGE" -lc \
    "command -v llvm-profdata-12 >/dev/null && command -v llvm-cov-12 >/dev/null && test -x '$BINARY' && strings '$BINARY' | grep -q __llvm_prf && test -x '$RESET_SCRIPT'" && [[ -x "$TEST_SCRIPT_HOST" ]]; then
  printf 'mysql\t%s\t%s\tok\tLLVM source coverage backend ready\n' "$IMAGE" "$BINARY" >> "$OUT/preflight_status.tsv"
else
  printf 'mysql\t%s\t%s\tfailed\tLLVM source coverage backend/reset/test unavailable\n' "$IMAGE" "$BINARY" >> "$OUT/preflight_status.tsv"
  exit 1
fi

prepare_queue() {
  local src="$1"
  local dst="$2"
  local mapping="$3"
  local limit="$4"
  mkdir -p "$dst"
  python3 - "$src" "$dst" "$mapping" "$limit" <<'PY'
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
mapping = Path(sys.argv[3])
limit = int(sys.argv[4])
files = sorted((p for p in src.iterdir() if p.is_file()), key=lambda p: (p.stat().st_mtime, p.name))
if not files:
    raise SystemExit(f"empty queue: {src}")

base = files[0].stat().st_mtime
raw_span_ms = max(0, round((files[-1].stat().st_mtime - base) * 1000))
normal_span_ms = 24 * 60 * 60 * 1000
selected = files[:limit] if limit else files

rows = []
for index, p in enumerate(selected):
    raw_ms = max(0, round((p.stat().st_mtime - base) * 1000))
    elapsed_ms = round(raw_ms * normal_span_ms / raw_span_ms) if raw_span_ms else 0
    name = re.sub(r",time:\d+(?=,|$)", "", p.name)
    replay_name = f"{name},time:{elapsed_ms}"
    out = dst / replay_name
    if out.exists() or out.is_symlink():
        replay_name = f"{name},replay_index:{index},time:{elapsed_ms}"
        out = dst / replay_name
    try:
        os.link(p, out)
        mode = "hardlink"
    except OSError:
        shutil.copy2(p, out)
        mode = "copy2"
    rows.append((p.name, replay_name, raw_ms, elapsed_ms, mode, p.stat().st_size))

with mapping.open("w", encoding="utf-8") as fp:
    fp.write("original\treplay_name\traw_time_ms\ttime_ms\tmode\tsize\n")
    for row in rows:
        fp.write("\t".join(map(str, row)) + "\n")

meta = mapping.with_name("queue_time_metadata.tsv")
with meta.open("w", encoding="utf-8") as fp:
    fp.write("source_queue\t%s\n" % src)
    fp.write("source_seed_count\t%s\n" % len(files))
    fp.write("staged_seed_count\t%s\n" % len(selected))
    fp.write("base_mtime_utc\t%s\n" % base)
    fp.write("raw_span_ms\t%s\n" % raw_span_ms)
    fp.write("normalized_span_ms\t%s\n" % normal_span_ms)
    fp.write("time_source\tmtime_relative_normalized_to_24h\n")
print(f"source_seed_count={len(files)} staged_seed_count={len(selected)} raw_span_ms={raw_span_ms}")
PY
}

run_one() {
  local run_id="$1"
  local run_work="$OUT/work/$run_id"
  local queue_dir="$run_work/queue_time_named"
  local source_queue="$CAMPAIGN/runs/mariadb/$run_id/output/mariadb_memory/default/queue"
  local out_dir="$run_work/out"
  local host_prefix="$out_dir/$run_id"
  local container_name="rq4_cov_${run_id}_$(date -u +%H%M%S)_${RANDOM}"
  local start_time end_time status rc seed_total

  mkdir -p "$run_work" "$out_dir"
  if [[ ! -d "$source_queue" ]]; then
    printf '%s\tfailed\t2\t\t\t0\n' "$run_id" > "$run_work/status.tsv"
    return 2
  fi
  if [[ ! -f "$run_work/queue_time_mapping.tsv" ]]; then
    prepare_queue "$source_queue" "$queue_dir" "$run_work/queue_time_mapping.tsv" "$SMOKE_SEEDS"
  fi
  seed_total=$(find "$queue_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "[$(date -Is)] start $run_id seeds=$seed_total"

  set +e
  docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
    -e GRIFFIN_CONTAINER=1 \
    -e LLVM_PROFILE_FILE='/tmp/rq2_prof/%p-%m.profraw' \
    -e LLVM_PROFDATA_BIN=llvm-profdata-12 \
    -e LLVM_COV_BIN=llvm-cov-12 \
    -e RQ2_REPLAY_SKIP_COV_EXPORT=1 \
    -e RQ2_REPLAY_FLUSH_SERVER_AT_CHECKPOINT=1 \
    -e RQ2_REPLAY_SERVER_CHECK_INTERVAL="$SERVER_CHECK_INTERVAL" \
    -e MARIADB_BASEDIR=/usr/local/mysql \
    -e MARIADBD_BINARY=/usr/local/mysql/bin/mariadbd \
    -e MARIADB_CLIENT=/usr/local/mysql/bin/mariadb \
    -e MARIADB_INSTALL_DB=/root/bin_original/usr/local/mysql/scripts/mysql_install_db \
    -v "$REPLAY_DIR":/rq2_scripts:ro \
    -v "$(dirname "$TEST_SCRIPT_HOST")":/rq4_scripts:ro \
    -v "$queue_dir":/rq2_queue:ro \
    -v "$out_dir":/rq2_out \
    --name "$container_name" --entrypoint /bin/bash "$IMAGE" \
    /rq2_scripts/container_replay_llvm_bucketed.sh \
      --dbms mysql --binary "$BINARY" --checkpoints-ms "$CHECKPOINTS_MS_CSV" \
      --seed-timeout "$SEED_TIMEOUT" --process-name my_8888 --out-prefix "/rq2_out/$run_id" \
      --reset-script "$RESET_SCRIPT" --test-script "$TEST_SCRIPT_CONTAINER" \
    > "$OUT/logs/${run_id}.docker.log" 2>&1
  rc=$?
  set -e
  end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  status=complete
  if [[ "$rc" -ne 0 ]]; then
    status=failed
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$run_id" "$status" "$rc" "$start_time" "$end_time" "$seed_total" > "$run_work/status.tsv"
  echo "[$(date -Is)] done $run_id status=$status rc=$rc"
  return "$rc"
}

wait_pair() {
  local failed=0
  local run_id pid rc
  for run_id in "${!PAIR_PIDS[@]}"; do
    pid="${PAIR_PIDS[$run_id]}"
    set +e
    wait "$pid"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      failed=1
    fi
  done
  return "$failed"
}

run_all_in_pairs() {
  declare -gA PAIR_PIDS=()
  local run_id
  for run_id in "${RUN_IDS[@]}"; do
    run_one "$run_id" &
    PAIR_PIDS["$run_id"]=$!
    if [[ ${#PAIR_PIDS[@]} -eq 2 ]]; then
      if ! wait_pair; then
        return 1
      fi
      PAIR_PIDS=()
    fi
  done
  if [[ ${#PAIR_PIDS[@]} -gt 0 ]]; then
    wait_pair
  fi
}

build_replay_index() {
  local index="$OUT/replay_status.tsv"
  printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tprofdata\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tstart_time\tend_time\n' > "$index"
  local run_id repeat status rc start end seed_total cp out_dir prof meta cp_seed_count row_status row_message
  for run_id in "${RUN_IDS[@]}"; do
    repeat="${run_id##*_r}"
    repeat="${repeat%%_*}"
    out_dir="$OUT/work/$run_id/out"
    status=failed
    rc=1
    start= end= seed_total=0
    if [[ -f "$OUT/work/$run_id/status.tsv" ]]; then
      IFS=$'\t' read -r _run status rc start end seed_total < "$OUT/work/$run_id/status.tsv"
    fi
    for cp in "${CHECKPOINT_ARR[@]}"; do
      prof="$out_dir/${run_id}_t${cp}.profdata"
      meta="$out_dir/${run_id}_t${cp}.meta.tsv"
      cp_seed_count="$seed_total"
      if [[ -f "$meta" ]]; then
        cp_seed_count=$(awk -F'\t' '$1=="seed_count"{print $2}' "$meta")
      fi
      row_status="$status"
      row_message=
      if [[ "$status" == complete && ! -f "$prof" ]]; then
        row_status=failed
        row_message="missing profdata for checkpoint $cp"
      elif [[ "$status" != complete ]]; then
        row_message="container replay rc=$rc"
      fi
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$run_id" "SQLeek-RQ4-w-o-M1" "mariadb" "$repeat" "$cp" "$prof" "$row_status" "$row_message" \
        "$IMAGE" "$BINARY" "$cp_seed_count" "$CAMPAIGN/runs/mariadb/$run_id/output/mariadb_memory/default/queue" \
        "$start" "$end" >> "$index"
    done
  done
}

stage_profdata() {
  local run_id repeat cp source destination
  for run_id in "${RUN_IDS[@]}"; do
    repeat="${run_id##*_r}"
    repeat="${repeat%%_*}"
    mkdir -p "$OUT/profdata/r$repeat"
    for cp in "${CHECKPOINT_ARR[@]}"; do
      source="$OUT/work/$run_id/out/${run_id}_t${cp}.profdata"
      destination="$OUT/profdata/r$repeat/sqleek_mariadb_r${repeat}_t${cp}.profdata"
      if [[ ! -f "$source" ]]; then
        echo "missing profdata: $source" >&2
        return 1
      fi
      ln "$source" "$destination" 2>/dev/null || cp -a "$source" "$destination"
    done
  done
}

REPEATS=1,2,3,4,5
if [[ "$SMOKE_SEEDS" -gt 0 ]]; then
  RUN_IDS=("${RUN_IDS[0]}")
  REPEATS=1
fi

run_all_in_pairs
build_replay_index
stage_profdata
python3 /root/SQLeek/experiment/RQ2/scripts/resummarize_mariadb_from_profdata.py \
  --out-dir "$OUT/data" \
  --target-regions "$OUT/target_regions.csv" \
  --profdata-root "$OUT/profdata" \
  --image "$IMAGE" \
  --binary "$BINARY" \
  --source-root /root/mariadb \
  --tool SQLeek-RQ4-w-o-M1 \
  --profdata-prefix sqleek \
  --repeats "$REPEATS"

{
  echo "out=$OUT"
  echo "campaign=$CAMPAIGN"
  echo "image=$IMAGE"
  echo "binary=$BINARY"
  echo "checkpoints_min=$CHECKPOINTS_MIN"
  echo "seed_timeout_sec=$SEED_TIMEOUT"
  echo "docker_mem=$DOCKER_MEM"
  echo "docker_shm=$DOCKER_SHM"
  echo "concurrency=2"
  echo "queue_time_source=mtime_relative_normalized_to_24h"
  echo "target_regions=$OUT/target_regions.csv"
} > "$OUT/REPLAY_SUMMARY.txt"

echo "$OUT"
