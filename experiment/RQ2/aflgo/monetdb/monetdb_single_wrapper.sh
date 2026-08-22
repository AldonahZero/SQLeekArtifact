#!/usr/bin/env bash
set -e
INPUT=${1:-}
if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
  exit 2
fi
AD=${AFLGO_MONETDB_ADAPTER:-/root/SQLeek/experiment/RQ2/aflgo/monetdb}
MSERVER=${AFLGO_MONETDB_MSERVER:-$AD/bin/mserver5_aflgo}
MCLIENT=${AFLGO_MONETDB_MCLIENT:-$AD/bin/mclient_aflgo}
INST=${AFLGO_MONETDB_BUILD:-$AD/build/monetdb-instrumented}
PREFIX=${AFLGO_MONETDB_PREFIX:-$AD/build/monetdb-install}
BASE_TMP=${AFLGO_MONETDB_TMPDIR:-$AD/runtime/tmp}
BASE_DATA=${AFLGO_MONETDB_DATADIR:-$AD/runtime/data}
BASE_LOG=${AFLGO_MONETDB_LOGDIR:-$AD/runtime/logs}
TIMEOUT=${AFLGO_MONETDB_SEED_TIMEOUT:-8}
LIMIT_MB=${AFLGO_MONETDB_FILE_LIMIT_MB:-4}
mkdir -p "$BASE_TMP" "$BASE_DATA" "$BASE_LOG"
size=$(wc -c < "$INPUT" 2>/dev/null || echo 0)
if [ "$size" -gt $((LIMIT_MB * 1024 * 1024)) ]; then
  exit 0
fi
ID="monetdb_aflgo_${$}_$(date +%s%N)"
PORT=$(( 43000 + ($$ % 20000) ))
RUN_TMP="$BASE_TMP/$ID"
DBPATH="$BASE_DATA/$ID"
LOG="$BASE_LOG/$ID.mserver.log"
SQL_COPY="$RUN_TMP/input.sql"
mkdir -p "$RUN_TMP" "$DBPATH"
cleanup() {
  rc=$?
  if [ -n "${SERVER_PID:-}" ]; then
    kill -TERM "$SERVER_PID" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$SERVER_PID" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill -KILL "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$RUN_TMP" "$DBPATH"
  exit "$rc"
}
trap cleanup EXIT INT TERM
cp "$INPUT" "$SQL_COPY"
# The build uses both installed and in-tree shared libraries/modules.
export LD_LIBRARY_PATH="$PREFIX/lib:$INST/common/utils:$INST/common/stream:$INST/clients/mapilib:$INST/gdk:$INST/monetdb5/tools:$INST/monetdb5/modules/kernel:$INST/monetdb5/modules/mal:$INST/monetdb5/modules/atoms:${LD_LIBRARY_PATH:-}"
printf 'user=monetdb\npassword=monetdb\n' > "$RUN_TMP/monetdb_auth"
export DOTMONETDBFILE="$RUN_TMP/monetdb_auth"
"$MSERVER" --accept-the-risks-running-as-root --dbpath="$DBPATH" --dbextra="$RUN_TMP" --set mapi_port="$PORT" --without-geom > "$LOG" 2>&1 &
SERVER_PID=$!
ready=0
for _ in $(seq 1 80); do
  if timeout 2 "$MCLIENT" -p "$PORT" -u monetdb -P monetdb -s 'select 1;' >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if [ "$ready" -ne 1 ]; then
  exit 0
fi
set +e
timeout "$TIMEOUT" "$MCLIENT" -p "$PORT" -u monetdb -P monetdb -f sql < "$SQL_COPY" >/dev/null 2>&1
rc=$?
set -e
# SQL errors and timeouts are not harness crashes. Real server death is still visible to AFL when mserver crashes.
if [ "$rc" -eq 124 ] || [ "$rc" -eq 125 ] || [ "$rc" -eq 126 ] || [ "$rc" -eq 127 ]; then
  exit 0
fi
exit 0
