#!/usr/bin/env bash
set -euo pipefail

QUEUE_DIR=/rq2_queue
OUT_DIR=/rq2_out
RUN_ID=mysql_replay
CHECKPOINTS_MIN=60,180,300,480,600,720,900,1200,1440
MAX_SEEDS=0
SEED_TIMEOUT=60
RESTART_EVERY=0
PROFILE_BUCKET_SIZE=${RQ2_PROFILE_BUCKET_SIZE:-200}
MIN_FREE_KB=${RQ2_MIN_FREE_KB:-10485760}
MERGE_FAILURE_MODE=${RQ2_MERGE_FAILURE_MODE:-warn}
PORT=8888
MYSQLD=${MYSQLD_BINARY:-/opt/mysql-llvmcov/bin/mysqld}
MYSQL=${MYSQL_CLIENT:-/opt/mysql-llvmcov/bin/mysql}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-18}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-18}
DB_NAME=${MYSQL_REPLAY_DB:-test}
PROFILE_BASE_DIR=/rq2_out/profiles
DATADIR=/dev/shm/rq2_mysql_datadir
SOCKET=/tmp/rq2_mysql.sock

while [[ $# -gt 0 ]]; do
  case "$1" in
    --queue-dir) QUEUE_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --max-seeds) MAX_SEEDS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --restart-every) RESTART_EVERY="$2"; shift 2 ;;
    --profile-bucket-size) PROFILE_BUCKET_SIZE="$2"; shift 2 ;;
    --min-free-kb) MIN_FREE_KB="$2"; shift 2 ;;
    --merge-failure-mode) MERGE_FAILURE_MODE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --mysqld) MYSQLD="$2"; shift 2 ;;
    --mysql) MYSQL="$2"; shift 2 ;;
    --database) DB_NAME="$2"; shift 2 ;;
    --profile-dir) PROFILE_BASE_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR" "$PROFILE_BASE_DIR" /workspace/logs
PROFILE_RUN_ID=${RQ2_PROFILE_RUN_ID:-${RUN_ID}_bucketed_$(date -u +%Y%m%d_%H%M%S)_$$}
PROFILE_DIR="$PROFILE_BASE_DIR/$PROFILE_RUN_ID"
RAW_PROFILE_DIR="$PROFILE_DIR/raw"
PROFDATA_DIR="$OUT_DIR/profdata"
BUCKET_PROFDATA_DIR="$PROFDATA_DIR/buckets"
mkdir -p "$RAW_PROFILE_DIR" "$BUCKET_PROFDATA_DIR"
chmod 0777 "$PROFILE_BASE_DIR" "$PROFILE_DIR" "$RAW_PROFILE_DIR"
export LLVM_PROFILE_FILE="${RQ2_LLVM_PROFILE_FILE:-$RAW_PROFILE_DIR/%p-%m.profraw}"
CUMULATIVE_PROFDATA="$PROFDATA_DIR/${RUN_ID}.cumulative.profdata"
bucket_id=0
profiles_merged_total=0
last_flush_seed_count=0

IFS=',' read -r -a checkpoint_min_raw <<< "$CHECKPOINTS_MIN"
checkpoint_ms=()
checkpoint_min=()
for raw in "${checkpoint_min_raw[@]}"; do
  min="${raw//[[:space:]]/}"
  [[ -n "$min" ]] || continue
  checkpoint_min+=("$min")
  checkpoint_ms+=("$((min * 60000))")
done
if [[ ${#checkpoint_ms[@]} -eq 0 ]]; then
  echo "no checkpoints requested" >&2
  exit 2
fi
max_checkpoint_ms="${checkpoint_ms[-1]}"

python3 - "$QUEUE_DIR" "$max_checkpoint_ms" "$MAX_SEEDS" > /tmp/rq2_selected_seeds.tsv <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

queue = Path(sys.argv[1])
checkpoint = int(sys.argv[2])
max_seeds = int(sys.argv[3])
rows = []
bad_time = []
for p in queue.glob("*"):
    if not p.is_file():
        continue
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", p.name)
    if not m:
        bad_time.append(p.name)
        continue
    t = int(m.group(1))
    if t <= checkpoint:
        rows.append((t, p.name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if max_seeds > 0:
    rows = rows[:max_seeds]
for t, name, path in rows:
    print(f"{t}\t{name}\t{path}")
if bad_time:
    print(f"skipped_bad_time_seeds={len(bad_time)}", file=sys.stderr)
    for name in bad_time[:20]:
        print(f"bad_time_seed={name}", file=sys.stderr)
PY

cp /tmp/rq2_selected_seeds.tsv "$OUT_DIR/${RUN_ID}.executed_seeds.tsv"
printf 'timestamp_utc\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
printf 'timestamp_utc\tseed_time\tseed_name\tseed_path\texit_code\n' > "$OUT_DIR/${RUN_ID}.input_status.tsv"
printf 'checkpoint_min\tseed_count\tprofile_count\tprofdata\treport_txt\n' > "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"
printf 'bucket_id\treason\tseed_count\traw_profile_count\traw_profile_bytes\tbucket_profdata\tcumulative_profdata\twarning_count\tmerge_log\n' > "$OUT_DIR/${RUN_ID}.profile_bucket_meta.tsv"

profile_bytes() {
  find "$PROFILE_DIR" -type f -name '*.profraw' -printf '%s\n' 2>/dev/null | awk '{s += $1} END {print s + 0}'
}

free_kb() {
  df -Pk "$OUT_DIR" | awk 'NR == 2 {print $4}'
}

stop_server() {
  if [[ -f /tmp/rq2_mysql.pid ]]; then
    kill -TERM "$(cat /tmp/rq2_mysql.pid)" >/dev/null 2>&1 || true
  fi
  pkill -TERM -f "$MYSQLD" >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    pgrep -f "$MYSQLD" >/dev/null 2>&1 || break
    sleep 0.5
  done
  pkill -KILL -f "$MYSQLD" >/dev/null 2>&1 || true
}

server_alive() {
  if [[ -f /tmp/rq2_mysql.pid ]] && kill -0 "$(cat /tmp/rq2_mysql.pid)" >/dev/null 2>&1; then
    return 0
  fi
  pgrep -f "$MYSQLD" >/dev/null 2>&1
}

wait_mysql() {
  for _ in $(seq 1 60); do
    if "$MYSQL" --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root -e 'SELECT 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_database() {
  "$MYSQL" --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root \
    -e "SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF" || true
  "$MYSQL" --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root \
    -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\`"
}

start_server() {
  rm -f "$DATADIR/mysqld-auto.cnf"
  "$MYSQLD" \
    --datadir="$DATADIR" \
    --socket="$SOCKET" \
    --port="$PORT" \
    --pid-file=/tmp/rq2_mysql.pid \
    --log-error="$OUT_DIR/${RUN_ID}.mysqld.log" \
    --skip-networking=0 \
    --mysqlx=0 \
    --disable-log-bin \
    --user=root >> "$OUT_DIR/${RUN_ID}.mysqld.stdout" 2>&1 &
  echo $! > /tmp/rq2_mysql.pid
  wait_mysql
  ensure_database
}

start_fresh_server() {
  stop_server
  rm -rf "$DATADIR"
  mkdir -p "$DATADIR"
  "$MYSQLD" --initialize-insecure --datadir="$DATADIR" --user=root >> "$OUT_DIR/${RUN_ID}.mysqld-init.log" 2>&1
  start_server
}

record_restart() {
  local seed_time="$1" seed_name="$2" seed="$3" rc="$4" action="$5"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_time" "$seed_name" "$seed" "$rc" "$action" \
    >> "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
}

flush_profiles() {
  local reason="$1"
  local seeds_seen="$2"
  stop_server
  sleep 2

  mapfile -t profiles < <(find "$PROFILE_DIR" -type f -name '*.profraw' | sort)
  local raw_count=${#profiles[@]}
  local raw_bytes
  raw_bytes=$(profile_bytes)
  if [[ "$raw_count" -eq 0 ]]; then
    last_flush_seed_count="$seeds_seen"
    return 0
  fi

  bucket_id=$((bucket_id + 1))
  local prefix="$BUCKET_PROFDATA_DIR/${RUN_ID}_bucket_$(printf '%06d' "$bucket_id")"
  local raw_list="${prefix}.raw_profiles.list"
  local bucket_profdata="${prefix}.profdata"
  local merge_log="${prefix}.merge.log"
  printf '%s\n' "${profiles[@]}" > "$raw_list"

  set +e
  "$LLVM_PROFDATA_BIN" merge --failure-mode="$MERGE_FAILURE_MODE" -sparse -f "$raw_list" -o "$bucket_profdata" > "$merge_log" 2>&1
  local merge_rc=$?
  set -e
  local warning_count
  warning_count=$(grep -c '^warning:' "$merge_log" 2>/dev/null || true)
  if [[ "$merge_rc" -ne 0 ]]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$bucket_id" "$reason" "$seeds_seen" "$raw_count" "$raw_bytes" "$bucket_profdata" "$CUMULATIVE_PROFDATA" "$warning_count" "$merge_log" \
      >> "$OUT_DIR/${RUN_ID}.profile_bucket_meta.tsv"
    echo "profile bucket merge failed: bucket=$bucket_id rc=$merge_rc log=$merge_log" >&2
    return "$merge_rc"
  fi

  if [[ -f "$CUMULATIVE_PROFDATA" ]]; then
    local tmp_cumulative="${CUMULATIVE_PROFDATA}.tmp.$$"
    "$LLVM_PROFDATA_BIN" merge --failure-mode="$MERGE_FAILURE_MODE" -sparse \
      "$CUMULATIVE_PROFDATA" "$bucket_profdata" -o "$tmp_cumulative" >> "$merge_log" 2>&1
    mv -f "$tmp_cumulative" "$CUMULATIVE_PROFDATA"
  else
    cp -f "$bucket_profdata" "$CUMULATIVE_PROFDATA"
  fi

  profiles_merged_total=$((profiles_merged_total + raw_count))
  find "$PROFILE_DIR" -type f -name '*.profraw' -delete
  last_flush_seed_count="$seeds_seen"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$bucket_id" "$reason" "$seeds_seen" "$raw_count" "$raw_bytes" "$bucket_profdata" "$CUMULATIVE_PROFDATA" "$warning_count" "$merge_log" \
    >> "$OUT_DIR/${RUN_ID}.profile_bucket_meta.tsv"
}

write_checkpoint() {
  local min="$1"
  local seeds_seen="$2"
  flush_profiles "checkpoint_t${min}" "$seeds_seen"
  if [[ ! -f "$CUMULATIVE_PROFDATA" ]]; then
    echo "checkpoint t${min}: no cumulative LLVM profile was generated; profile_pattern=$LLVM_PROFILE_FILE" >&2
    return 5
  fi
  local prefix="$OUT_DIR/${RUN_ID}_t${min}"
  cp -f "$CUMULATIVE_PROFDATA" "${prefix}.profdata.tmp"
  mv -f "${prefix}.profdata.tmp" "${prefix}.profdata"
  "$LLVM_COV_BIN" report "$MYSQLD" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt.tmp"
  mv -f "${prefix}.report.txt.tmp" "${prefix}.report.txt"
  printf '%s\t%s\t%s\t%s\t%s\n' "$min" "$seeds_seen" "$profiles_merged_total" "${prefix}.profdata" "${prefix}.report.txt" >> "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"
}

maybe_flush_for_disk() {
  local seed_time="$1" seed_name="$2" seed="$3" rc="$4" seeds_seen="$5"
  if [[ "$MIN_FREE_KB" -le 0 ]]; then
    return 1
  fi
  local current_free
  current_free=$(free_kb)
  if [[ "$current_free" -lt "$MIN_FREE_KB" ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "profile_flush_low_disk_${current_free}kb"
    flush_profiles "low_disk_${current_free}kb" "$seeds_seen"
    start_server
    return 0
  fi
  return 1
}

if ! start_fresh_server; then
  echo "initial MySQL startup failed" >&2
  exit 3
fi

seed_count=0
checkpoint_idx=0
while IFS=$'\t' read -r seed_time seed_name seed; do
  [[ -n "${seed:-}" ]] || continue
  while [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} && "$seed_time" -gt "${checkpoint_ms[$checkpoint_idx]}" ]]; do
    min="${checkpoint_min[$checkpoint_idx]}"
    record_restart "$seed_time" "$seed_name" "$seed" 0 "checkpoint_t${min}_flush"
    write_checkpoint "$min" "$seed_count"
    checkpoint_idx=$((checkpoint_idx + 1))
    if [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; then
      start_server
    fi
  done
  if [[ $checkpoint_idx -ge ${#checkpoint_ms[@]} ]]; then
    break
  fi

  seed_count=$((seed_count + 1))
  set +e
  timeout "$SEED_TIMEOUT" "$MYSQL" --binary-mode=1 --force --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root "$DB_NAME" < "$seed" >> "$OUT_DIR/${RUN_ID}.replay.stdout" 2>> "$OUT_DIR/${RUN_ID}.replay.stderr"
  rc=$?
  set -e
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_time" "$seed_name" "$seed" "$rc" >> "$OUT_DIR/${RUN_ID}.input_status.tsv"

  if ! server_alive; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "server_missing_flush"
    flush_profiles "server_missing" "$seed_count"
    if ! start_fresh_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_failed"
      exit 6
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_ok"
  elif [[ "$RESTART_EVERY" -gt 0 && $((seed_count % RESTART_EVERY)) -eq 0 ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_flush"
    flush_profiles "periodic_restart" "$seed_count"
    if ! start_fresh_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_failed"
      exit 7
    fi
  elif [[ "$PROFILE_BUCKET_SIZE" -gt 0 && $((seed_count - last_flush_seed_count)) -ge "$PROFILE_BUCKET_SIZE" ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "profile_bucket_flush"
    flush_profiles "profile_bucket" "$seed_count"
    start_server
  else
    maybe_flush_for_disk "$seed_time" "$seed_name" "$seed" "$rc" "$seed_count" || true
  fi
done < /tmp/rq2_selected_seeds.tsv

while [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; do
  min="${checkpoint_min[$checkpoint_idx]}"
  record_restart "$max_checkpoint_ms" "end" "" 0 "checkpoint_t${min}_flush"
  write_checkpoint "$min" "$seed_count"
  checkpoint_idx=$((checkpoint_idx + 1))
done

stop_server
