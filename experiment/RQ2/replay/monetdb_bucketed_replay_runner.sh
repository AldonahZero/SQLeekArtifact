#!/usr/bin/env bash
set -euo pipefail

QUEUE_DIR=/rq2_queue
OUT_DIR=/rq2_out
RUN_ID=monetdb_replay
CHECKPOINTS_MIN=60,180,300,480,600,720,900,1200,1440
MAX_SEEDS=0
SEED_TIMEOUT=120
RESTART_EVERY=0
PORT=50000
MSERVER=${MSERVER_BINARY:-/monetdb_llvmcov/bin/mserver5}
MCLIENT=${MCLIENT_BINARY:-/root/bin_original/usr/local/bin/mclient}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}
PROFILE_BASE_DIR=/tmp/rq2_prof
DATA_BASE_DIR=/workspace/fuzzing/monetdb_data

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
    --mserver) MSERVER="$2"; shift 2 ;;
    --mclient) MCLIENT="$2"; shift 2 ;;
    --profile-dir) PROFILE_BASE_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR" "$PROFILE_BASE_DIR" "$DATA_BASE_DIR" /workspace/logs
RUN_STAMP=${RQ2_RUN_STAMP:-$(date -u +%Y%m%d_%H%M%S)_$$}
PROFILE_DIR="$PROFILE_BASE_DIR/${RUN_ID}_${RUN_STAMP}"
mkdir -p "$PROFILE_DIR"
chmod 0777 "$PROFILE_BASE_DIR" "$PROFILE_DIR"
export LLVM_PROFILE_FILE="${RQ2_LLVM_PROFILE_FILE:-$PROFILE_DIR/%p-%m.profraw}"

MSERVER_DIR=$(dirname "$MSERVER")
MCLIENT_DIR=$(dirname "$MCLIENT")
LIB_DIR="$MSERVER_DIR/../lib"
MODULE_DIR1="$LIB_DIR/monetdb5-56.0.0"
MODULE_DIR2="$LIB_DIR/monetdb5"
CLIENT_LIB_DIR="$MCLIENT_DIR/../lib"
CLIENT_LD_LIBRARY_PATH="$CLIENT_LIB_DIR:/root/bin_original/usr/local/lib/:$LIB_DIR:$MODULE_DIR1:$MODULE_DIR2"
PROC_NAME="mon${PORT}"
PROC_PATH="$DATA_BASE_DIR/$PROC_NAME"
SEED_RUNNER=/tmp/rq2_monetdb_seed_runner.sh

configure_runtime_helpers() {
  python3 - "$PORT" "$MSERVER_DIR" "$PROC_NAME" "$SEED_RUNNER" <<'PY'
from pathlib import Path
import sys
port, mserver_dir, proc_name, seed_runner = sys.argv[1:5]
config = Path('/workspace/configs/odbc.ini')
section = None
out = []
for line in config.read_text(encoding='utf-8').splitlines():
    stripped = line.strip()
    if stripped.startswith('[') and stripped.endswith(']'):
        section = stripped
    if section == '[dsnForFuzzer]' and stripped.startswith('PORT'):
        line = f'PORT = {port}'
    elif section == '[dsnForFuzzer]' and stripped.startswith('ResetScriptLevel1='):
        line = f'ResetScriptLevel1=/workspace/scripts/reset_lv1.sh {port} {mserver_dir}/ > /dev/null 2> /dev/null'
    out.append(line)
config.write_text('\n'.join(out) + '\n', encoding='utf-8')
seed_script = """#!/usr/bin/env bash
set -euo pipefail
seed="$1"
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT
cat "$seed" > "$tmpfile"
source /workspace/scripts/base_env.sh
source /workspace/scripts/env.sh
SQLSIM_TIMEOUT_MS=100000 /workspace/bld_griffin/autodriver_odbc_v5_aflpp dsnForFuzzer < "$tmpfile" || true
if ! pgrep {proc_name} 1>&2
then
    echo "Server crashed." 1>&2
    kill -SIGABRT $$
else
    echo "Server normal." 1>&2
fi
""".format(proc_name=proc_name)
Path(seed_runner).write_text(seed_script, encoding='utf-8')
PY
  chmod +x "$SEED_RUNNER"
}

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

python3 - "$QUEUE_DIR" "$max_checkpoint_ms" "$MAX_SEEDS" "$OUT_DIR/${RUN_ID}.seed_audit.tsv" > /tmp/rq2_monetdb_selected_seeds.tsv <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

queue = Path(sys.argv[1])
checkpoint = int(sys.argv[2])
max_seeds = int(sys.argv[3])
audit = Path(sys.argv[4])
rows = []
bad = []
zero = 0
for p in queue.glob("*"):
    if not p.is_file():
        continue
    m = re.search(r"time:(\d+)", p.name)
    if not m:
        bad.append(str(p))
        continue
    t = int(m.group(1))
    if t == 0:
        zero += 1
    if t <= checkpoint:
        rows.append((t, p.name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if max_seeds > 0:
    rows = rows[:max_seeds]
with audit.open("w", encoding="utf-8") as fp:
    fp.write("metric\tvalue\n")
    fp.write(f"queue_dir\t{queue}\n")
    fp.write(f"selected_seed_count\t{len(rows)}\n")
    fp.write(f"bad_time_parse\t{len(bad)}\n")
    fp.write(f"time_zero\t{zero}\n")
    if rows:
        fp.write(f"max_selected_time_ms\t{rows[-1][0]}\n")
for t, name, path in rows:
    print(f"{t}\t{name}\t{path}")
PY

cp /tmp/rq2_monetdb_selected_seeds.tsv "$OUT_DIR/${RUN_ID}.executed_seeds.tsv"
printf 'timestamp_utc\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
printf 'checkpoint_min\tseed_count\tprofile_count\tprofdata\treport_txt\n' > "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"
: > "$OUT_DIR/${RUN_ID}.mserver.log"
: > "$OUT_DIR/${RUN_ID}.replay.stdout"
: > "$OUT_DIR/${RUN_ID}.replay.stderr"
configure_runtime_helpers

record_restart() {
  local seed_time="$1" seed_name="$2" seed="$3" rc="$4" action="$5"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_time" "$seed_name" "$seed" "$rc" "$action" \
    >> "$OUT_DIR/${RUN_ID}.server_restarts.tsv"
}

stop_server() {
  local pid=""
  if [[ -f "/tmp/${RUN_ID}.pid" ]]; then
    pid="$(cat "/tmp/${RUN_ID}.pid" 2>/dev/null || true)"
    [[ -n "$pid" ]] && kill -TERM "$pid" >/dev/null 2>&1 || true
  fi
  pkill -TERM -x "$PROC_NAME" >/dev/null 2>&1 || true
  pkill -TERM -f "$PROC_PATH" >/dev/null 2>&1 || true
  pkill -TERM -f "$MSERVER" >/dev/null 2>&1 || true
  pkill -TERM -x mserver5 >/dev/null 2>&1 || true
  for _ in $(seq 1 40); do
    if ! pgrep -x "$PROC_NAME" >/dev/null 2>&1 \
       && ! pgrep -f "$PROC_PATH" >/dev/null 2>&1 \
       && ! pgrep -f "$MSERVER" >/dev/null 2>&1 \
       && ! pgrep -x mserver5 >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  pkill -KILL -x "$PROC_NAME" >/dev/null 2>&1 || true
  pkill -KILL -f "$PROC_PATH" >/dev/null 2>&1 || true
  pkill -KILL -f "$MSERVER" >/dev/null 2>&1 || true
  pkill -KILL -x mserver5 >/dev/null 2>&1 || true
  rm -f "/tmp/${RUN_ID}.pid"
  for _ in $(seq 1 10); do
    if ! LD_LIBRARY_PATH="$CLIENT_LD_LIBRARY_PATH" "$MCLIENT" -p "$PORT" < /dev/null >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 0
}

server_alive() {
  if [[ -f "/tmp/${RUN_ID}.pid" ]] && kill -0 "$(cat "/tmp/${RUN_ID}.pid")" >/dev/null 2>&1; then
    return 0
  fi
  pgrep -x "$PROC_NAME" >/dev/null 2>&1
}

wait_monetdb() {
  for _ in $(seq 1 60); do
    if LD_LIBRARY_PATH="$CLIENT_LD_LIBRARY_PATH" "$MCLIENT" -p "$PORT" < /dev/null >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

monetdb_query_ok() {
  timeout 5 env LD_LIBRARY_PATH="$CLIENT_LD_LIBRARY_PATH" "$MCLIENT" -p "$PORT" < /dev/null >/dev/null 2>&1
}

start_server_once() {
  rm -rf "$DATA_BASE_DIR/data_${PORT}" "$PROC_PATH"
  mkdir -p "$DATA_BASE_DIR/data_${PORT}"
  ln -s "$MSERVER" "$PROC_PATH"
  printf 'user=monetdb\npassword=monetdb\n' > /root/.monetdb
  env \
    LLVM_PROFILE_FILE="$LLVM_PROFILE_FILE" \
    LD_LIBRARY_PATH="$LIB_DIR:$MODULE_DIR1:$MODULE_DIR2" \
    "$PROC_PATH" --accept-the-risks-running-as-root --in-memory --set mapi_port="$PORT" \
    >> "$OUT_DIR/${RUN_ID}.mserver.log" 2>&1 &
  echo $! > "/tmp/${RUN_ID}.pid"
  wait_monetdb
}

start_server() {
  stop_server
  if start_server_once; then
    return 0
  fi
  record_restart "0" "server_start" "" 0 "start_retry_after_bind_or_connect_failure"
  stop_server
  sleep 2
  start_server_once
}

merge_checkpoint() {
  local min="$1"
  local seeds_seen="$2"
  stop_server
  sleep 2
  local prefix="$OUT_DIR/${RUN_ID}_t${min}"
  local profile_list="${prefix}.profiles.list"
  find "$PROFILE_DIR" -type f -name '*.profraw' -size +0c | sort > "$profile_list"
  local profile_count
  profile_count=$(wc -l < "$profile_list" | tr -d ' ')
  if [[ "$profile_count" -eq 0 ]]; then
    echo "checkpoint t${min}: no LLVM profile files were generated; profile_pattern=$LLVM_PROFILE_FILE" >&2
    return 5
  fi
  "$LLVM_PROFDATA_BIN" merge -sparse -f "$profile_list" -o "${prefix}.profdata"
  "$LLVM_COV_BIN" report "$MSERVER" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt"
  printf '%s\t%s\t%s\t%s\t%s\n' "$min" "$seeds_seen" "$profile_count" "${prefix}.profdata" "${prefix}.report.txt" >> "$OUT_DIR/${RUN_ID}.checkpoint_meta.tsv"
}

if ! start_server; then
  echo "initial MonetDB startup failed" >&2
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
      if ! start_server; then
        record_restart "$seed_time" "$seed_name" "$seed" 0 "checkpoint_restart_failed"
        exit 6
      fi
    fi
  done
  if [[ $checkpoint_idx -ge ${#checkpoint_ms[@]} ]]; then
    break
  fi

  seed_count=$((seed_count + 1))
  set +e
  timeout "$SEED_TIMEOUT" "$SEED_RUNNER" "$seed" >> "$OUT_DIR/${RUN_ID}.replay.stdout" 2>> "$OUT_DIR/${RUN_ID}.replay.stderr"
  rc=$?
  set -e

  if ! server_alive; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "server_missing_restart"
    if ! start_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_failed"
      exit 7
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "restart_ok"
  elif [[ "$rc" -ne 0 ]] && ! monetdb_query_ok; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "connectivity_broken_restart"
    if ! start_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "connectivity_restart_failed"
      exit 8
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "connectivity_restart_ok"
  elif [[ "$RESTART_EVERY" -gt 0 && $((seed_count % RESTART_EVERY)) -eq 0 ]]; then
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart"
    if ! start_server; then
      record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_failed"
      exit 9
    fi
    record_restart "$seed_time" "$seed_name" "$seed" "$rc" "periodic_restart_ok"
  fi
done < /tmp/rq2_monetdb_selected_seeds.tsv

while [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; do
  min="${checkpoint_min[$checkpoint_idx]}"
  record_restart "$max_checkpoint_ms" "end" "" 0 "checkpoint_t${min}_flush"
  merge_checkpoint "$min" "$seed_count"
  checkpoint_idx=$((checkpoint_idx + 1))
  if [[ $checkpoint_idx -lt ${#checkpoint_ms[@]} ]]; then
    if ! start_server; then
      record_restart "$max_checkpoint_ms" "end" "" 0 "final_checkpoint_restart_failed"
      exit 10
    fi
  fi
done

stop_server
