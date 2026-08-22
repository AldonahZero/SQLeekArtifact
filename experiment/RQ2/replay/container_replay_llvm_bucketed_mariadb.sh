#!/usr/bin/env bash
set -euo pipefail

DBMS=
BINARY=
CHECKPOINTS_MS=
SEED_TIMEOUT=120
OUT_PREFIX=/rq2_out/replay
RESET_SCRIPT=/workspace/scripts/reset_lv1.sh
TEST_SCRIPT=/workspace/scripts/testt
PROCESS_NAME=
SERVER_CHECK_INTERVAL=${RQ2_REPLAY_SERVER_CHECK_INTERVAL:-1}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}
LLVM_COV_EXPORT_ARGS=(--skip-functions --skip-expansions)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbms) DBMS="$2"; shift 2 ;;
    --binary) BINARY="$2"; shift 2 ;;
    --checkpoints-ms) CHECKPOINTS_MS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --out-prefix) OUT_PREFIX="$2"; shift 2 ;;
    --reset-script) RESET_SCRIPT="$2"; shift 2 ;;
    --test-script) TEST_SCRIPT="$2"; shift 2 ;;
    --process-name) PROCESS_NAME="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$DBMS" || -z "$BINARY" || -z "$CHECKPOINTS_MS" ]]; then
  echo "--dbms, --binary and --checkpoints-ms are required" >&2
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

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MS"

python3 - "$CHECKPOINTS_MS" > /tmp/rq2_seed_buckets <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

checkpoints = sorted({int(x) for x in sys.argv[1].split(",") if x})
if not checkpoints:
    raise SystemExit("no checkpoints")
max_checkpoint = max(checkpoints)
rows = []
bad_time = []
for p in Path("/rq2_queue").glob("*"):
    if not p.is_file():
        continue
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", p.name)
    if not m:
        bad_time.append(p.name)
        continue
    t = int(m.group(1))
    if t <= max_checkpoint:
        rows.append((t, p.name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if bad_time:
    print(f"skipped_bad_time_seeds={len(bad_time)}", file=sys.stderr)
    for name in bad_time[:20]:
        print(f"bad_time_seed={name}", file=sys.stderr)

prev = -1
for cp in checkpoints:
    bucket = [row for row in rows if prev < row[0] <= cp]
    cumulative = [row for row in rows if row[0] <= cp]
    cp_min = cp // 60000
    with open(f"/tmp/rq2_bucket_{cp_min}.tsv", "w", encoding="utf-8") as fp:
        for t, name, path in bucket:
            fp.write(f"{t}\t{name}\t{path}\n")
    with open(f"/tmp/rq2_cumulative_{cp_min}.tsv", "w", encoding="utf-8") as fp:
        for t, name, path in cumulative:
            fp.write(f"{t}\t{name}\t{path}\n")
    prev = cp
PY

terminate_server() {
  local process_names=()
  [[ -n "$PROCESS_NAME" ]] && process_names+=("$PROCESS_NAME")
  if [[ "${RQ2_REPLAY_FLUSH_SERVER_AT_CHECKPOINT:-0}" == "1" && "$DBMS" == "mysql" ]]; then
    process_names+=(my_8888 mariadbd mysqld)
  fi
  local process_name
  for process_name in "${process_names[@]}"; do
    pkill -TERM "$process_name" >/dev/null 2>&1 || true
  done
  for _ in $(seq 1 20); do
    local alive=0
    for process_name in "${process_names[@]}"; do
      if pgrep "$process_name" >/dev/null 2>&1; then
        alive=1
        break
      fi
    done
    [[ "$alive" -eq 0 ]] && break
    sleep 0.5
  done
  for process_name in "${process_names[@]}"; do
    pkill -INT "$process_name" >/dev/null 2>&1 || true
  done
  sleep 1
  for process_name in "${process_names[@]}"; do
    pkill -KILL "$process_name" >/dev/null 2>&1 || true
  done
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
  echo "server process missing after seed; restarting dbms=$DBMS checkpoint_min=$checkpoint_min seed=$seed" >&2
  if ! reset_server "restart"; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_failed"
    echo "reset failed after server loss; dbms=$DBMS checkpoint_min=$checkpoint_min seed=$seed" >&2
    exit 6
  fi
  if ! wait_for_server; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_no_server"
    echo "server did not come back after reset; dbms=$DBMS checkpoint_min=$checkpoint_min seed=$seed" >&2
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

snapshot_coverage() {
  local cp_min="$1"
  local prefix="${OUT_PREFIX}_t${cp_min}"
  local cumulative="/tmp/rq2_cumulative_${cp_min}.tsv"
  local seed_count
  seed_count=$(wc -l < "$cumulative" | tr -d ' ')
  cp "$cumulative" "${prefix}.executed_seeds.tsv"
  find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -ls > "${prefix}.profiles.list" || true
  shopt -s nullglob
  local profiles=("$PROFILE_DIR"/*.profraw "$PROFILE_DIR"/*.profdata)
  if [[ ${#profiles[@]} -eq 0 ]]; then
    echo "no LLVM profile files were generated at checkpoint ${cp_min}; seed_count=${seed_count}" >&2
    exit 5
  fi
  "$LLVM_PROFDATA_BIN" merge -sparse "${profiles[@]}" -o "${prefix}.profdata"
  if [[ "${RQ2_REPLAY_SKIP_COV_EXPORT:-0}" == "1" ]]; then
    printf 'coverage_export_skipped\t1\n' > "${prefix}.cov_json.skipped.txt"
  else
    "$LLVM_COV_BIN" export "${LLVM_COV_EXPORT_ARGS[@]}" -format=text "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.cov.json"
    "$LLVM_COV_BIN" report "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt"
  fi
  printf 'seed_count\t%s\nprofile_count\t%s\n' "$seed_count" "${#profiles[@]}" > "${prefix}.meta.tsv"
}

flush_server_for_profile() {
  if [[ "${RQ2_REPLAY_FLUSH_SERVER_AT_CHECKPOINT:-0}" != "1" || "$DBMS" != "mysql" ]]; then
    return 0
  fi
  terminate_server
  if ! reset_server "checkpoint"; then
    echo "checkpoint server reset failed while flushing LLVM profiles" >&2
    exit 6
  fi
  if ! wait_for_server; then
    echo "checkpoint server did not come back after LLVM profile flush" >&2
    exit 6
  fi
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
    for cp_ms in "${CHECKPOINT_ARR[@]}"; do
      cp_min=$((cp_ms / 60000))
      bucket="/tmp/rq2_bucket_${cp_min}.tsv"
      while IFS=$'\t' read -r _time _name seed; do
        [[ -n "${seed:-}" ]] || continue
        run_dbms_seed "$cp_min" "$_time" "$_name" "$seed"
      done < "$bucket"
      flush_server_for_profile
      snapshot_coverage "$cp_min"
    done
    terminate_server
    ;;
  sqlite)
    n=0
    for cp_ms in "${CHECKPOINT_ARR[@]}"; do
      cp_min=$((cp_ms / 60000))
      bucket="/tmp/rq2_bucket_${cp_min}.tsv"
      while IFS=$'\t' read -r _time _name seed; do
        [[ -n "${seed:-}" ]] || continue
        n=$((n + 1))
        db="/tmp/rq2_sqlite_${n}.db"
        timeout "$SEED_TIMEOUT" "$BINARY" "$db" < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr" || true
        rm -f "$db" "$db-journal" "$db-wal" "$db-shm"
      done < "$bucket"
      snapshot_coverage "$cp_min"
    done
    ;;
  *)
    echo "unsupported dbms executor: $DBMS" >&2
    exit 4
    ;;
esac
