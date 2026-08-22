#!/usr/bin/env bash
set -euo pipefail

DBMS=
BINARY=
CHECKPOINT_MS=86400000
MAX_SEEDS=0
SEED_TIMEOUT=120
OUT_PREFIX=/rq2_out/replay
RESET_SCRIPT=/workspace/scripts/reset_lv1.sh
TEST_SCRIPT=/workspace/scripts/testt
PROCESS_NAME=
SERVER_CHECK_INTERVAL=${RQ2_REPLAY_SERVER_CHECK_INTERVAL:-1}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbms) DBMS="$2"; shift 2 ;;
    --binary) BINARY="$2"; shift 2 ;;
    --checkpoint-ms) CHECKPOINT_MS="$2"; shift 2 ;;
    --max-seeds) MAX_SEEDS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --out-prefix) OUT_PREFIX="$2"; shift 2 ;;
    --reset-script) RESET_SCRIPT="$2"; shift 2 ;;
    --test-script) TEST_SCRIPT="$2"; shift 2 ;;
    --process-name) PROCESS_NAME="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$DBMS" || -z "$BINARY" ]]; then
  echo "--dbms and --binary are required" >&2
  exit 2
fi

PROFILE_DIR=/tmp/rq2_prof
mkdir -p "$(dirname "$OUT_PREFIX")" "$PROFILE_DIR" /workspace/logs /workspace/fuzzing
chmod 0777 "$PROFILE_DIR"
rm -rf "$PROFILE_DIR"/*
export LLVM_PROFILE_FILE="${LLVM_PROFILE_FILE:-%c/tmp/rq2_prof/%p-%m.profraw}"

if ! [[ "$SERVER_CHECK_INTERVAL" =~ ^[0-9]+$ ]] || [[ "$SERVER_CHECK_INTERVAL" -lt 1 ]]; then
  SERVER_CHECK_INTERVAL=1
fi

python3 - "$CHECKPOINT_MS" "$MAX_SEEDS" > /tmp/rq2_selected_seeds.tsv <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

checkpoint = int(sys.argv[1])
max_seeds = int(sys.argv[2])
rows = []
for p in Path("/rq2_queue").glob("*"):
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

seed_count=$(wc -l < /tmp/rq2_selected_seeds.tsv | tr -d ' ')
cp /tmp/rq2_selected_seeds.tsv "${OUT_PREFIX}.executed_seeds.tsv"

terminate_server() {
  if [[ -n "$PROCESS_NAME" ]]; then
    pkill -TERM "$PROCESS_NAME" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      pgrep "$PROCESS_NAME" >/dev/null 2>&1 || break
      sleep 0.5
    done
    pkill -INT "$PROCESS_NAME" >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL "$PROCESS_NAME" >/dev/null 2>&1 || true
  fi
}

server_alive() {
  local candidates=()
  [[ -n "$PROCESS_NAME" ]] && candidates+=("$PROCESS_NAME")
  case "$DBMS" in
    postgres) candidates+=("pg_c_8888" "postgres") ;;
    mysql) candidates+=("my_8888" "mysqld") ;;
  esac
  local name
  for name in "${candidates[@]}"; do
    [[ -n "$name" ]] || continue
    if pgrep -x "$name" >/dev/null 2>&1 || pgrep "$name" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

wait_for_server() {
  for _ in $(seq 1 20); do
    server_alive && return 0
    sleep 0.5
  done
  return 1
}

reset_server() {
  local reason="$1"
  local save_dir=""
  if [[ "$reason" != "initial" ]]; then
    save_dir=$(mktemp -d /tmp/rq2_prof_keep.XXXXXX)
    shopt -s nullglob
    local existing_profiles=("$PROFILE_DIR"/*.profraw "$PROFILE_DIR"/*.profdata)
    if [[ ${#existing_profiles[@]} -gt 0 ]]; then
      cp -a "${existing_profiles[@]}" "$save_dir"/
    fi
    shopt -u nullglob
  fi

  set +e
  "$RESET_SCRIPT" 8888 "$BINARY" >> "${OUT_PREFIX}.reset.log" 2>&1
  local rc=$?
  set -e

  if [[ -n "$save_dir" ]]; then
    mkdir -p "$PROFILE_DIR"
    chmod 0777 "$PROFILE_DIR"
    shopt -s nullglob
    local saved_profiles=("$save_dir"/*)
    if [[ ${#saved_profiles[@]} -gt 0 ]]; then
      cp -an "${saved_profiles[@]}" "$PROFILE_DIR"/ 2>/dev/null || true
    fi
    shopt -u nullglob
    rm -rf "$save_dir"
  fi
  return "$rc"
}

DBMS_SEED_INDEX=0
RESTART_LOG="${OUT_PREFIX}.server_restarts.tsv"

record_server_restart() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  local exit_code="$5"
  local action="$6"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DBMS" "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "$action" \
    >> "$RESTART_LOG"
}

maybe_recover_server() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  local exit_code="$5"
  DBMS_SEED_INDEX=$((DBMS_SEED_INDEX + 1))
  if [[ "$exit_code" -eq 0 && $((DBMS_SEED_INDEX % SERVER_CHECK_INTERVAL)) -ne 0 ]]; then
    return 0
  fi
  if server_alive; then
    return 0
  fi

  record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "server_missing_restart"
  echo "server process missing after seed; restarting dbms=$DBMS seed=$seed" >&2
  if ! reset_server "restart"; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_failed"
    echo "reset failed after server loss; dbms=$DBMS seed=$seed" >&2
    exit 6
  fi
  if ! wait_for_server; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_no_server"
    echo "server did not come back after reset; dbms=$DBMS seed=$seed" >&2
    exit 6
  fi
  record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_ok"
}

run_dbms_seed() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  set +e
  timeout "$SEED_TIMEOUT" "$TEST_SCRIPT" < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr"
  local rc=$?
  set -e
  maybe_recover_server "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$rc"
}

case "$DBMS" in
  mysql|postgres)
    if [[ ! -x "$RESET_SCRIPT" || ! -x "$TEST_SCRIPT" ]]; then
      echo "missing reset/test script: $RESET_SCRIPT $TEST_SCRIPT" >&2
      exit 3
    fi
    printf 'timestamp_utc\tdbms\tcheckpoint_min\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "$RESTART_LOG"
    : > "${OUT_PREFIX}.reset.log"
    if ! reset_server "initial" || ! wait_for_server; then
      echo "initial reset failed or server did not start: $RESET_SCRIPT $BINARY" >&2
      exit 3
    fi
    while IFS=$'\t' read -r _time _name seed; do
      [[ -n "${seed:-}" ]] || continue
      run_dbms_seed "" "$_time" "$_name" "$seed"
    done < /tmp/rq2_selected_seeds.tsv
    terminate_server
    ;;
  sqlite)
    n=0
    while IFS=$'\t' read -r _time _name seed; do
      n=$((n + 1))
      db="/tmp/rq2_sqlite_${n}.db"
      timeout "$SEED_TIMEOUT" "$BINARY" "$db" < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr" || true
      rm -f "$db" "$db-journal" "$db-wal" "$db-shm"
    done < /tmp/rq2_selected_seeds.tsv
    ;;
  *)
    echo "unsupported dbms executor: $DBMS" >&2
    exit 4
    ;;
esac

find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -ls > "${OUT_PREFIX}.profiles.list" || true
shopt -s nullglob
profiles=("$PROFILE_DIR"/*.profraw "$PROFILE_DIR"/*.profdata)
if [[ ${#profiles[@]} -eq 0 ]]; then
  echo "no LLVM profile files were generated; seed_count=$seed_count profile_pattern=$LLVM_PROFILE_FILE" >&2
  exit 5
fi

"$LLVM_PROFDATA_BIN" merge -sparse "${profiles[@]}" -o "${OUT_PREFIX}.profdata"
"$LLVM_COV_BIN" export -format=text "$BINARY" -instr-profile="${OUT_PREFIX}.profdata" > "${OUT_PREFIX}.cov.json"
"$LLVM_COV_BIN" report "$BINARY" -instr-profile="${OUT_PREFIX}.profdata" > "${OUT_PREFIX}.report.txt"
printf 'seed_count\t%s\nprofile_count\t%s\n' "$seed_count" "${#profiles[@]}" > "${OUT_PREFIX}.meta.tsv"
