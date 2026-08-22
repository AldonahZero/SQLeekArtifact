#!/usr/bin/env bash
set -euo pipefail

QUEUE_DIR=/rq2_queue
OUT_DIR=/rq2_out
RUN_ID=mysql_replay
CHECKPOINTS_MIN=60,180,300,480,600,720,900,1200,1440
MAX_SEEDS=0
SEED_TIMEOUT=60
RESTART_EVERY=0
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
mkdir -p "$PROFILE_DIR"
chmod 0777 "$PROFILE_BASE_DIR" "$PROFILE_DIR"
export LLVM_PROFILE_FILE="${RQ2_LLVM_PROFILE_FILE:-$PROFILE_DIR/%p-%m.profraw}"
LAST_PROFDATA=

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
mode_counts = {"time": 0, "orig": 0, "mtime": 0}
queue_files = [
    p for p in sorted(queue.iterdir())
    if p.is_file() and p.name != "README.txt" and not p.name.startswith(".")
]
start = min((p.stat().st_mtime for p in queue_files), default=0.0)
for p in queue_files:
    name = p.name
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", name)
    if m:
        t = int(m.group(1))
        mode = "time"
    elif re.search(r"(?:^|,)orig:", name):
        t = 0
        mode = "orig"
    else:
        t = max(0, int(round((p.stat().st_mtime - start) * 1000)))
        mode = "mtime"
    mode_counts[mode] += 1
    if t <= checkpoint:
        rows.append((t, name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if max_seeds > 0:
    rows = rows[:max_seeds]
for t, name, path in rows:
    print(f"{t}\t{name}\t{path}")
print(
    "seed_time_modes "
    f"time={mode_counts['time']} "
    f"orig={mode_counts['orig']} "
    f"mtime={mode_counts['mtime']}",
    file=sys.stderr,
)
print(f"selected_seed_count={len(rows)}", file=sys.stderr)
PY

cp /tmp/rq2_selected_seeds.tsv "$OUT_DIR/${RUN_ID}.executed_seeds.tsv"
printf 'timestamp_utc\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
printf 'checkpoint_min\tseed_count\tprofile_count\tprofdata\treport_txt\n' > "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"

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
    if mysql_ready_once; then
      return 0
    fi
    sleep 1
  done
  return 1
}

mysql_ready_once() {
  timeout 5 "$MYSQL" --connect-timeout=2 --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root -e 'SELECT 1' >/dev/null 2>&1
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

restart_server_keep_datadir() {
  stop_server
  start_server
}

record_restart() {
  local seed_time="$1" seed_name="$2" seed="$3" rc="$4" action="$5"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_time" "$seed_name" "$seed" "$rc" "$action" \
    >> "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
}

merge_checkpoint() {
  local min="$1"
  local seeds_seen="$2"
  stop_server
  sleep 2
  local prefix="$OUT_DIR/${RUN_ID}_t${min}"
  local profile_list="${prefix}.profiles.list"
  local merge_list="${prefix}.merge_inputs.list"
  mapfile -t profiles < <(find "$PROFILE_DIR" -type f -name '*.profraw' | sort)
  : > "$profile_list"
  if [[ ${#profiles[@]} -gt 0 ]]; then
    printf '%s\n' "${profiles[@]}" > "$profile_list"
  fi
  : > "$merge_list"
  if [[ -n "${LAST_PROFDATA:-}" && -f "$LAST_PROFDATA" ]]; then
    printf '%s\n' "$LAST_PROFDATA" >> "$merge_list"
  fi
  if [[ ${#profiles[@]} -gt 0 ]]; then
    printf '%s\n' "${profiles[@]}" >> "$merge_list"
  fi
  local merge_count
  merge_count=$(wc -l < "$merge_list" | tr -d ' ')
  if [[ "$merge_count" -eq 0 ]]; then
    echo "checkpoint t${min}: no LLVM profile files were generated; profile_pattern=$LLVM_PROFILE_FILE" >&2
    return 5
  fi
  "$LLVM_PROFDATA_BIN" merge -sparse -f "$merge_list" -o "${prefix}.profdata"
  "$LLVM_COV_BIN" report "$MYSQLD" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt"
  LAST_PROFDATA="${prefix}.profdata"
  find "$PROFILE_DIR" -type f -name '*.profraw' -delete
  printf '%s\t%s\t%s\t%s\t%s\n' "$min" "$seeds_seen" "$merge_count" "${prefix}.profdata" "${prefix}.report.txt" >> "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"
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
    merge_checkpoint "$min" "$seed_count"
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
  if ! server_alive; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "server_missing_restart"
    if ! start_fresh_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_failed"
      exit 6
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_ok"
  elif [[ "$rc" -ne 0 ]] && ! mysql_ready_once; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "auth_failed_fresh_restart"
    if ! start_fresh_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "auth_failed_restart_failed"
      exit 8
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "auth_failed_restart_ok"
  elif [[ "$RESTART_EVERY" -gt 0 && $((seed_count % RESTART_EVERY)) -eq 0 ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart"
    if ! restart_server_keep_datadir; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_keep_datadir_failed"
      if ! start_fresh_server; then
        record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_fresh_failed"
        exit 7
      fi
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_fresh_ok"
    fi
  fi
done < /tmp/rq2_selected_seeds.tsv

while [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; do
  min="${checkpoint_min[$checkpoint_idx]}"
  record_restart "$max_checkpoint_ms" "end" "" 0 "checkpoint_t${min}_flush"
  merge_checkpoint "$min" "$seed_count"
  checkpoint_idx=$((checkpoint_idx + 1))
  if [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; then
    start_server
  fi
done

stop_server
find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -ls > "$OUT_DIR/${RUN_ID}.profiles.list" || true
printf 'seed_count\t%s\nprofile_count\t%s\nrestart_every\t%s\ncheckpoints_min\t%s\nprofile_dir\t%s\nprofile_pattern\t%s\n' \
  "$seed_count" \
  "$(find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) | wc -l | tr -d ' ')" \
  "$RESTART_EVERY" \
  "$CHECKPOINTS_MIN" \
  "$PROFILE_DIR" \
  "$LLVM_PROFILE_FILE" > "$OUT_DIR/${RUN_ID}.meta.tsv"
