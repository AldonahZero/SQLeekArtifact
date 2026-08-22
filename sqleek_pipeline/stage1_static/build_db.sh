#!/bin/bash
set -e

cd /root/SQLeek
STAGE_DIR="/root/SQLeek/sqleek_pipeline/stage1_static"
mkdir -p "$STAGE_DIR/output"
exec > >(tee -a "$STAGE_DIR/output/build.log") 2>&1

CODEQL_BIN="${CODEQL_BIN:-codeql}"
DB_ROOT="/root/SQLeek/codeql_dbs"
SRC_ROOT="${SQLEEK_DBMS_SRC_ROOT:-/root/SQLeek/dbms_src}"
TIMEOUT_SECONDS="${CODEQL_DB_TIMEOUT:-1800}"

mkdir -p "$DB_ROOT" "$SRC_ROOT" "$STAGE_DIR/output/codeql_results"

log() {
  printf '[build_db] %s\n' "$*"
}

mark_skipped() {
  local dbms="$1"
  local reason="$2"
  mkdir -p "$STAGE_DIR/output/codeql_results/$dbms"
  printf '%s\n' "$reason" > "$STAGE_DIR/output/codeql_results/$dbms/SKIPPED"
  log "$dbms skipped: $reason"
}

require_codeql() {
  if ! command -v "$CODEQL_BIN" >/dev/null 2>&1; then
    log "CodeQL binary not found: $CODEQL_BIN"
    return 1
  fi
}

build_database() {
  local dbms="$1"
  local src_dir="$2"
  local build_cmd="$3"
  local db_dir="$DB_ROOT/$dbms"

  if [ ! -d "$src_dir" ]; then
    mark_skipped "$dbms" "source directory missing: $src_dir"
    return 0
  fi

  rm -rf "$db_dir"
  log "creating CodeQL database for $dbms from $src_dir"
  if timeout "$TIMEOUT_SECONDS" "$CODEQL_BIN" database create "$db_dir" \
      --language=cpp \
      --source-root="$src_dir" \
      --command="$build_cmd"; then
    log "$dbms CodeQL database created at $db_dir"
  else
    mark_skipped "$dbms" "CodeQL database creation failed or timed out"
  fi
}

if ! require_codeql; then
  for dbms in sqlite postgres mysql; do
    mark_skipped "$dbms" "CodeQL unavailable"
  done
  exit 0
fi

build_database "sqlite" \
  "$SRC_ROOT/sqlite" \
  "cc -O0 -g -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_JSON1 -c sqlite3.c -o sqlite3.o"

build_database "postgres" \
  "$SRC_ROOT/postgres" \
  "./configure --without-readline --without-zlib && make -j$(nproc)"

build_database "mysql" \
  "$SRC_ROOT/mysql" \
  "cmake -S . -B build -DDOWNLOAD_BOOST=1 -DWITH_BOOST=build/boost -DWITH_UNIT_TESTS=OFF && cmake --build build -j$(nproc)"

log "CodeQL database build stage complete"
