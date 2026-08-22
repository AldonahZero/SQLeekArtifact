#!/usr/bin/env bash
set -euo pipefail

SQLRIGHT_ROOT="${SQLRIGHT_ROOT:-/opt/sqlright}"
FUZZ_ROOT="$SQLRIGHT_ROOT/SQLite/docker/fuzz_root"
FUZZER="$FUZZ_ROOT/afl-fuzz"
INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
OUTPUT_DIR="${OUTPUT_DIR:-/workspace/output}/monetdb_memory/default"
TARGETS="${SQLASER_TARGETS:-$FUZZ_ROOT/sqlaser/monetdb_target_chains.tsv}"
WRAPPER="${MONETDB_WRAPPER:-/usr/local/bin/monetdb_single_wrapper.sh}"
CORE="${CORE:-0}"
ORACLE="${ORACLE:-NOREC}"
DURATION="${DURATION:-24h}"

test -x "$FUZZER"
test -x "$WRAPPER"
test -s "$TARGETS"
test -d "$INPUT_DIR"
find "$INPUT_DIR" -type f -size +0c -print -quit | grep -q . || {
  echo "SQLaser MonetDB input directory has no non-empty SQL seeds: $INPUT_DIR" >&2
  exit 2
}

mkdir -p "$OUTPUT_DIR" /workspace/runtime /workspace/logs
cd "$FUZZ_ROOT"

export SQLEEK_DBMS=monetdb
export SQLASER_ENABLE=1
export SQLASER_TARGETS="$TARGETS"
export SQLEEK_MONETDB_ROOT="${SQLEEK_MONETDB_ROOT:-/opt/dbms}"
export SQLEEK_MONETDB_MSERVER="${SQLEEK_MONETDB_MSERVER:-/opt/dbms/bin/mserver5}"
export SQLEEK_MONETDB_MCLIENT="${SQLEEK_MONETDB_MCLIENT:-/opt/dbms/bin/mclient}"
export SQLEEK_MONETDB_SEED_TIMEOUT="${SQLEEK_MONETDB_SEED_TIMEOUT:-8}"
export SQLEEK_MONETDB_TMPDIR="${SQLEEK_MONETDB_TMPDIR:-/workspace/runtime/monetdb/tmp}"
export SQLEEK_MONETDB_DATADIR="${SQLEEK_MONETDB_DATADIR:-/workspace/runtime/monetdb/data}"
export SQLEEK_MONETDB_LOGDIR="${SQLEEK_MONETDB_LOGDIR:-/workspace/logs/monetdb_server}"
export SQLEEK_MONETDB_COVERAGE_ONLY=1
export SQLRIGHT_SYNC_COVERAGE_ONLY=1
export SQLRIGHT_SYNC_KEEP_NONCOV=1
export AFL_MAP_SIZE=262144
export AFL_IGNORE_PROBLEMS=1
export AFL_IGNORE_PROBLEMS_COVERAGE=1
export AFL_NO_FORKSRV=1
export AFL_SKIP_BIN_CHECK=1
export AFL_NO_AFFINITY=1
export LD_LIBRARY_PATH="/opt/dbms/lib:/opt/dbms/lib/monetdb5:${LD_LIBRARY_PATH:-}"

exec timeout --signal=TERM --kill-after=30s "$DURATION" \
  "$FUZZER" \
    -i "$INPUT_DIR" \
    -o "$OUTPUT_DIR" \
    -c "$CORE" \
    -O "$ORACLE" \
    -t "${AFL_TIMEOUT:-20000}+" \
    -- "$WRAPPER" @@
