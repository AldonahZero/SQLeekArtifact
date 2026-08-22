#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-}"
if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
  exit 2
fi

ROOT="${SQLEEK_MONETDB_ROOT:-/opt/dbms}"
MSERVER="${SQLEEK_MONETDB_MSERVER:-$ROOT/bin/mserver5}"
MCLIENT="${SQLEEK_MONETDB_MCLIENT:-$ROOT/bin/mclient}"
BASE_TMP="${SQLEEK_MONETDB_TMPDIR:-${RUNTIME_DIR:-/workspace/runtime}/monetdb/tmp}"
BASE_DATA="${SQLEEK_MONETDB_DATADIR:-${RUNTIME_DIR:-/workspace/runtime}/monetdb/data}"
BASE_LOG="${SQLEEK_MONETDB_LOGDIR:-${LOG_DIR:-/workspace/logs}/monetdb_server}"
TIMEOUT_SECONDS="${SQLEEK_MONETDB_SEED_TIMEOUT:-8}"
LIMIT_MB="${SQLEEK_MONETDB_FILE_LIMIT_MB:-4}"
PORT_BASE="${SQLEEK_PORT_START:-43000}"

[ -x "$MSERVER" ] || exit 127
[ -x "$MCLIENT" ] || exit 127
mkdir -p "$BASE_TMP" "$BASE_DATA" "$BASE_LOG"

size=$(wc -c < "$INPUT" 2>/dev/null || echo 0)
if [ "$size" -le 0 ] || [ "$size" -gt $((LIMIT_MB * 1024 * 1024)) ]; then
  exit 0
fi

run_id="monetdb_${$}_$(date +%s%N)"
port=$((PORT_BASE + ($$ % 20000)))
run_tmp="$BASE_TMP/$run_id"
dbpath="$BASE_DATA/$run_id"
log_file="$BASE_LOG/$run_id.mserver.log"
sql_copy="$run_tmp/input.sql"
server_pid=""
mkdir -p "$run_tmp" "$dbpath"

cleanup() {
  rc=$?
  if [ -n "$server_pid" ]; then
    kill -TERM "$server_pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 "$server_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill -KILL "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$run_tmp" "$dbpath"
  exit "$rc"
}
trap cleanup EXIT INT TERM

cp "$INPUT" "$sql_copy"
printf 'user=monetdb\npassword=monetdb\n' > "$run_tmp/monetdb_auth"
export DOTMONETDBFILE="$run_tmp/monetdb_auth"
export LD_LIBRARY_PATH="$ROOT/lib:$ROOT/lib/monetdb5:${LD_LIBRARY_PATH:-}"
export AFL_MAP_SIZE="${AFL_MAP_SIZE:-262144}"
export AFL_IGNORE_PROBLEMS="${AFL_IGNORE_PROBLEMS:-1}"
export AFL_IGNORE_PROBLEMS_COVERAGE="${AFL_IGNORE_PROBLEMS_COVERAGE:-1}"

server_args=(
  "$MSERVER"
  --dbpath="$dbpath"
  --dbextra="$run_tmp"
  --set "mapi_port=$port"
  --without-geom
)
if [ "${SQLEEK_MONETDB_IN_MEMORY:-0}" = "1" ]; then
  server_args+=(--in-memory)
fi
if [ "$(id -u)" -eq 0 ]; then
  server_args+=(--accept-the-risks-running-as-root)
fi

"${server_args[@]}" > "$log_file" 2>&1 &
server_pid=$!

ready=0
for _ in $(seq 1 100); do
  if timeout 2 "$MCLIENT" -p "$port" -s 'select 1;' >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if [ "$ready" -ne 1 ]; then
  exit 1
fi

set +e
timeout "$TIMEOUT_SECONDS" "$MCLIENT" -p "$port" -f sql < "$sql_copy" >/dev/null 2>&1
client_rc=$?
set -e

# SQL errors and statement timeouts are fuzzing outcomes, not harness crashes.
# If the server died while processing the input, surface that to AFL.
if ! kill -0 "$server_pid" >/dev/null 2>&1; then
  wait "$server_pid" >/dev/null 2>&1 || true
  exit 1
fi

case "$client_rc" in
  124|125|126|127) exit 0 ;;
  *) exit 0 ;;
esac
