#!/usr/bin/env bash
set -euo pipefail

SQLRIGHT_ROOT="${SQLRIGHT_ROOT:-/opt/sqlright}"
FUZZ_ROOT="$SQLRIGHT_ROOT/MySQL/docker/fuzz_root"
INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
OUTPUT_DIR="${OUTPUT_DIR:-/workspace/output}"
TARGETS="${SQLASER_TARGETS:-$FUZZ_ROOT/sqlaser/mariadb_target_chains.tsv}"
CORE="${CORE:-0}"
NUM_CONCURRENT="${NUM_CONCURRENT:-1}"
ORACLE="${ORACLE:-NOREC}"
DURATION="${DURATION:-24h}"

test -x "$SQLRIGHT_ROOT/MySQL/docker/src/afl-fuzz"
test -f "$FUZZ_ROOT/run_parallel.py"
test -s "$TARGETS"
test -d "$INPUT_DIR"
find "$INPUT_DIR" -type f -size +0c -print -quit | grep -q . || {
  echo "SQLaser MariaDB input directory has no non-empty SQL seeds: $INPUT_DIR" >&2
  exit 2
}

mkdir -p "$OUTPUT_DIR"
cd "$FUZZ_ROOT"

export SQLEEK_DBMS=mariadb
export SQLEEK_MYSQL_ROOT="${SQLEEK_MYSQL_ROOT:-/opt/dbms}"
export SQLEEK_INPUT_DIR="$INPUT_DIR"
export SQLEEK_SQLRIGHT_AFL="$SQLRIGHT_ROOT/MySQL/docker/src/afl-fuzz"
export SQLEEK_OUTPUT_LAYOUT=1
export SQLEEK_START_CORE="$CORE"
export SQLEEK_NUM_CONCURRENT="$NUM_CONCURRENT"
export SQLEEK_AFL_TIMEOUT="${AFL_TIMEOUT:-300}"
export SQLEEK_MEMORY_LIMIT="${MEMORY_LIMIT:-4000}"
export SQLASER_ENABLED=1
export SQLASER_TARGETS="$TARGETS"
export AFL_NO_AFFINITY=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_SKIP_CPUFREQ=1
export LD_LIBRARY_PATH="$SQLRIGHT_ROOT/MySQL/docker/src/parser/mysql-server/bld/library_output_directory:${LD_LIBRARY_PATH:-}"

exec timeout --signal=TERM --kill-after=30s "$DURATION" \
  python3 run_parallel.py \
    -o "$OUTPUT_DIR" \
    --start-core "$CORE" \
    --num-concurrent "$NUM_CONCURRENT" \
    --oracle "$ORACLE"
