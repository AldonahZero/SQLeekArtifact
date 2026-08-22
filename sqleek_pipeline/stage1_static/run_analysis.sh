#!/bin/bash
set -e

cd /root/SQLeek
STAGE_DIR="/root/SQLeek/sqleek_pipeline/stage1_static"
mkdir -p "$STAGE_DIR/output"
exec > >(tee -a "$STAGE_DIR/output/build.log") 2>&1

CODEQL_BIN="${CODEQL_BIN:-codeql}"
DB_ROOT="/root/SQLeek/codeql_dbs"
QUERY_DIR="$STAGE_DIR/queries"
RESULT_ROOT="$STAGE_DIR/output/codeql_results"

mkdir -p "$RESULT_ROOT"

log() {
  printf '[run_analysis] %s\n' "$*"
}

empty_results() {
  local dbms="$1"
  local reason="$2"
  mkdir -p "$RESULT_ROOT/$dbms"
  : > "$RESULT_ROOT/$dbms/memory_sinks.csv"
  : > "$RESULT_ROOT/$dbms/logic_patterns.csv"
  : > "$RESULT_ROOT/$dbms/callchain_extractor.csv"
  printf '%s\n' "$reason" > "$RESULT_ROOT/$dbms/SKIPPED"
  log "$dbms analysis skipped: $reason"
}

if ! command -v "$CODEQL_BIN" >/dev/null 2>&1; then
  for dbms in sqlite postgres mysql; do
    empty_results "$dbms" "CodeQL unavailable"
  done
  exit 0
fi

for dbms in sqlite postgres mysql; do
  db_dir="$DB_ROOT/$dbms"
  out_dir="$RESULT_ROOT/$dbms"
  mkdir -p "$out_dir"

  if [ ! -d "$db_dir" ]; then
    empty_results "$dbms" "CodeQL database missing: $db_dir"
    continue
  fi

  for query in memory_sinks logic_patterns callchain_extractor; do
    ql="$QUERY_DIR/$query.ql"
    bqrs="$out_dir/$query.bqrs"
    csv="$out_dir/$query.csv"

    log "running $query for $dbms"
    if "$CODEQL_BIN" query run "$ql" --database="$db_dir" --output="$bqrs"; then
      "$CODEQL_BIN" bqrs decode "$bqrs" --format=csv --output="$csv"
      log "wrote $csv"
    else
      : > "$csv"
      log "$query failed for $dbms; wrote empty CSV for fallback parsing"
    fi
  done
done

log "CodeQL analysis stage complete"
