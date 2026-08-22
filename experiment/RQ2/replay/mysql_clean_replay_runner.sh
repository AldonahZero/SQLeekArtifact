#!/usr/bin/env bash
set -euo pipefail

QUEUE_DIR=/rq2_queue
OUT_PREFIX=/rq2_out/mysql_replay
CHECKPOINT_MS=86400000
MAX_SEEDS=0
SEED_TIMEOUT=60
RESTART_EVERY=0
WRITE_COV_JSON=1
PORT=8888
MYSQLD=${MYSQLD_BINARY:-/opt/mysql-llvmcov/bin/mysqld}
MYSQL=${MYSQL_CLIENT:-/opt/mysql-llvmcov/bin/mysql}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-18}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-18}
PROFILE_BASE_DIR=/tmp/rq2_prof
DATADIR=/dev/shm/rq2_mysql_datadir
SOCKET=/tmp/rq2_mysql.sock

while [[ $# -gt 0 ]]; do
  case "$1" in
    --queue-dir) QUEUE_DIR="$2"; shift 2 ;;
    --out-prefix) OUT_PREFIX="$2"; shift 2 ;;
    --checkpoint-ms) CHECKPOINT_MS="$2"; shift 2 ;;
    --max-seeds) MAX_SEEDS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --restart-every) RESTART_EVERY="$2"; shift 2 ;;
    --skip-cov-json) WRITE_COV_JSON=0; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --mysqld) MYSQLD="$2"; shift 2 ;;
    --mysql) MYSQL="$2"; shift 2 ;;
    --profile-dir) PROFILE_BASE_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

PROFILE_RUN_ID=${RQ2_PROFILE_RUN_ID:-mysql_clean_$(date -u +%Y%m%d_%H%M%S)_$$}
PROFILE_DIR="$PROFILE_BASE_DIR/$PROFILE_RUN_ID"
mkdir -p "$(dirname "$OUT_PREFIX")" "$PROFILE_BASE_DIR" "$PROFILE_DIR" /workspace/logs
chmod 0777 "$PROFILE_BASE_DIR" "$PROFILE_DIR"
export LLVM_PROFILE_FILE="${RQ2_LLVM_PROFILE_FILE:-$PROFILE_DIR/%p-%m.profraw}"

python3 - "$QUEUE_DIR" "$CHECKPOINT_MS" "$MAX_SEEDS" > /tmp/rq2_selected_seeds.tsv <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

queue = Path(sys.argv[1])
checkpoint = int(sys.argv[2])
max_seeds = int(sys.argv[3])
rows = []
for p in queue.glob("*"):
    if not p.is_file():
        continue
    m = re.search(r"time:(\d+)", p.name)
    t = int(m.group(1)) if m else 0
    if t <= checkpoint:
        rows.append((t, p.name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if max_seeds > 0:
    rows = rows[:max_seeds]
for t, name, path in rows:
    print(f"{t}\t{name}\t{path}")
PY

cp /tmp/rq2_selected_seeds.tsv "${OUT_PREFIX}.executed_seeds.tsv"
printf 'timestamp_utc\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "${OUT_PREFIX}.server_restarts.tsv"

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

start_fresh_server() {
  stop_server
  rm -rf "$DATADIR"
  mkdir -p "$DATADIR"
  "$MYSQLD" --initialize-insecure --datadir="$DATADIR" --user=root >> "${OUT_PREFIX}.mysqld-init.log" 2>&1
  start_server
}

start_server() {
  "$MYSQLD" \
    --datadir="$DATADIR" \
    --socket="$SOCKET" \
    --port="$PORT" \
    --pid-file=/tmp/rq2_mysql.pid \
    --log-error="${OUT_PREFIX}.mysqld.log" \
    --skip-networking=0 \
    --mysqlx=0 \
    --disable-log-bin \
    --user=root >> "${OUT_PREFIX}.mysqld.stdout" 2>&1 &
  echo $! > /tmp/rq2_mysql.pid
  wait_mysql
}

restart_server_keep_datadir() {
  stop_server
  start_server
}

record_restart() {
  local seed_time="$1" seed_name="$2" seed="$3" rc="$4" action="$5"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_time" "$seed_name" "$seed" "$rc" "$action" \
    >> "${OUT_PREFIX}.server_restarts.tsv"
}

if ! start_fresh_server; then
  echo "initial MySQL startup failed" >&2
  exit 3
fi

seed_count=0
while IFS=$'\t' read -r seed_time seed_name seed; do
  [[ -n "${seed:-}" ]] || continue
  seed_count=$((seed_count + 1))
  set +e
  timeout "$SEED_TIMEOUT" "$MYSQL" --binary-mode=1 --force --protocol=tcp -h 127.0.0.1 -P "$PORT" -u root < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr"
  rc=$?
  set -e
  if ! server_alive; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "server_missing_restart"
    if ! start_fresh_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_failed"
      exit 6
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_ok"
  elif [[ "$RESTART_EVERY" -gt 0 && $((seed_count % RESTART_EVERY)) -eq 0 ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart"
    if ! restart_server_keep_datadir; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_failed"
      exit 7
    fi
  fi
done < /tmp/rq2_selected_seeds.tsv

stop_server
sleep 2

find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -ls > "${OUT_PREFIX}.profiles.list" || true
mapfile -t profiles < <(find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) | sort)
if [[ ${#profiles[@]} -eq 0 ]]; then
  echo "no LLVM profile files were generated; profile_pattern=$LLVM_PROFILE_FILE" >&2
  exit 5
fi

"$LLVM_PROFDATA_BIN" merge -sparse "${profiles[@]}" -o "${OUT_PREFIX}.profdata"
"$LLVM_COV_BIN" report "$MYSQLD" -instr-profile="${OUT_PREFIX}.profdata" > "${OUT_PREFIX}.report.txt"
if [[ "$WRITE_COV_JSON" -eq 1 ]]; then
  "$LLVM_COV_BIN" export -format=text "$MYSQLD" -instr-profile="${OUT_PREFIX}.profdata" > "${OUT_PREFIX}.cov.json"
else
  printf 'skipped full cov.json; rerun llvm-cov export from profdata if needed\n' > "${OUT_PREFIX}.cov_json.skipped.txt"
fi
printf 'seed_count\t%s\nprofile_count\t%s\nrestart_every\t%s\nwrite_cov_json\t%s\nprofile_dir\t%s\nprofile_pattern\t%s\n' \
  "$(wc -l < /tmp/rq2_selected_seeds.tsv | tr -d ' ')" \
  "${#profiles[@]}" \
  "$RESTART_EVERY" \
  "$WRITE_COV_JSON" \
  "$PROFILE_DIR" \
  "$LLVM_PROFILE_FILE" > "${OUT_PREFIX}.meta.tsv"
