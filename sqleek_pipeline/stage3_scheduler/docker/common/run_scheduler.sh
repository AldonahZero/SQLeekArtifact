#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
STAGE_DIR="/opt/sqleek/stage3_scheduler"
SQLRIGHT_ROOT="/opt/sqlright"
RUN_OUTPUT="${OUTPUT_DIR}"
SCHED_LOG="${LOG_DIR}/scheduler.log"
SQLRIGHT_LOG="${LOG_DIR}/sqlright.log"
SCHED_PID=""
SQLRIGHT_PID=""
STATS_COMPAT_PID=""
QUEUE_MTIME_PID=""
CORPUS_IMPORT_PID=""
STAGED_IMPORT_PID=""
INSTANCE_ID=""

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$RUNTIME_DIR"

duration_seconds() {
  python3 - "$1" <<'PY'
import sys
s=sys.argv[1].strip().lower()
scale={'s':1,'m':60,'h':3600,'d':86400}
if not s:
    print(0)
elif s[-1:] in scale:
    print(int(float(s[:-1])*scale[s[-1]]))
else:
    print(int(float(s)))
PY
}

log() {
  printf '[sqleek-stage3] %s\n' "$*" | tee -a "$LOG_DIR/entrypoint.log"
}

first_cpu_from_list() {
  local value="$1"
  local first="${value%%,*}"
  printf '%s
' "${first%%-*}"
}

normalize_dbms_name() {
  case "$DBMS" in
    postgresql) DBMS="postgres"; export DBMS ;;
  esac
}

detect_instance_id() {
  if [ -n "${CPU_ID:-}" ]; then
    printf '%s
' "$CPU_ID"
    return 0
  fi
  if [ -r /sys/fs/cgroup/cpuset.cpus.effective ]; then
    first_cpu_from_list "$(cat /sys/fs/cgroup/cpuset.cpus.effective)"
    return 0
  fi
  if [ -r /sys/fs/cgroup/cpuset/cpuset.cpus ]; then
    first_cpu_from_list "$(cat /sys/fs/cgroup/cpuset/cpuset.cpus)"
    return 0
  fi
  local cpus
  cpus="$(awk -F'\t' '/Cpus_allowed_list/ {print $2; exit}' /proc/self/status)"
  first_cpu_from_list "${cpus:-0}"
}


cleanup() {
  rc=$?
  log "cleanup start rc=$rc"
  if [ -n "$SCHED_PID" ] && kill -0 "$SCHED_PID" 2>/dev/null; then
    kill -TERM "$SCHED_PID" 2>/dev/null || true
    wait "$SCHED_PID" 2>/dev/null || true
  fi
  if [ -n "$SQLRIGHT_PID" ] && kill -0 "$SQLRIGHT_PID" 2>/dev/null; then
    kill -TERM "$SQLRIGHT_PID" 2>/dev/null || true
    wait "$SQLRIGHT_PID" 2>/dev/null || true
  fi
  stop_fuzzer_stats_compat || true
  if [ -n "$CORPUS_IMPORT_PID" ] && kill -0 "$CORPUS_IMPORT_PID" 2>/dev/null; then
    kill -TERM "$CORPUS_IMPORT_PID" 2>/dev/null || true
    wait "$CORPUS_IMPORT_PID" 2>/dev/null || true
  fi
  if [ -n "$STAGED_IMPORT_PID" ] && kill -0 "$STAGED_IMPORT_PID" 2>/dev/null; then
    kill -TERM "$STAGED_IMPORT_PID" 2>/dev/null || true
    wait "$STAGED_IMPORT_PID" 2>/dev/null || true
  fi
  if [ -n "$QUEUE_MTIME_PID" ] && kill -0 "$QUEUE_MTIME_PID" 2>/dev/null; then
    kill -TERM "$QUEUE_MTIME_PID" 2>/dev/null || true
    wait "$QUEUE_MTIME_PID" 2>/dev/null || true
  fi
  /opt/sqleek/stage3_scheduler/docker/common/cleanup.sh || true
  log "cleanup complete"
  exit "$rc"
}
trap cleanup INT TERM EXIT

require_non_empty_seed_dir() {
  [ -d "$SEED_DIR" ] || { log "missing seed dir: $SEED_DIR"; exit 2; }
  find "$SEED_DIR" -type f -size +0c -print -quit | grep -q . || { log "seed dir has no non-empty files: $SEED_DIR"; exit 2; }
}

require_targets() {
  [ -d "$TARGET_DIR" ] || { log "missing target dir: $TARGET_DIR"; exit 2; }
  if ! find "$TARGET_DIR" -maxdepth 1 -type f \( -name 'callchains.json' -o -name "${DBMS}_memory.txt" -o -name "${DBMS}_distance.json" -o -name 'distance.json' \) -print -quit | grep -q .; then
    log "target dir lacks callchains/target/distance artifacts for $DBMS: $TARGET_DIR"
    exit 2
  fi
}

require_runtime_tools() {
  command -v python3 >/dev/null
  [ -x /opt/aflplusplus/afl-fuzz ] || { log "missing AFL++ afl-fuzz"; exit 2; }
  [ -x /opt/aflplusplus/afl-showmap ] || { log "missing AFL++ afl-showmap"; exit 2; }
  [ -d "$SQLRIGHT_ROOT" ] || { log "missing SQLRight root: $SQLRIGHT_ROOT"; exit 2; }
  case "$DBMS" in
    mysql|mariadb)
      [ -x /opt/dbms/bin/mysqld ] || { log "missing mysqld under /opt/dbms/bin"; exit 2; }
      ;;
    postgres|postgresql)
      [ -x /opt/dbms/bin/postgres ] || { log "missing postgres under /opt/dbms/bin"; exit 2; }
      ;;
    monetdb)
      [ -x /opt/dbms/bin/monetdbd ] || [ -x /opt/dbms/bin/mserver5 ] || { log "missing MonetDB runtime under /opt/dbms/bin"; exit 2; }
      ;;
  esac
}

validate_scheduler_config() {
  if [ -n "${SCHEDULER_CONFIG:-}" ]; then
    python3 -m json.tool "$SCHEDULER_CONFIG" >/dev/null
  fi
}

start_fuzzer_stats_compat() {
  local fuzzer_dir="$1"
  local command_line="$2"
  python3 "$STAGE_DIR/docker/common/fuzzer_stats_compat.py" \
    --fuzzer-dir "$fuzzer_dir" \
    --dbms "$DBMS" \
    --run-id "$RUN_ID" \
    --interval "${FUZZER_STATS_COMPAT_INTERVAL:-5}" \
    --command-line "$command_line" \
    --scheduler-log "$SCHED_LOG" \
    --log-file "$LOG_DIR/fuzzer_stats_compat.log" &
  STATS_COMPAT_PID=$!
  log "fuzzer_stats compat pid=$STATS_COMPAT_PID dir=$fuzzer_dir"
}

stop_fuzzer_stats_compat() {
  if [ -n "$STATS_COMPAT_PID" ] && kill -0 "$STATS_COMPAT_PID" 2>/dev/null; then
    kill -TERM "$STATS_COMPAT_PID" 2>/dev/null || true
    wait "$STATS_COMPAT_PID" 2>/dev/null || true
  fi
  STATS_COMPAT_PID=""
}

prepare_sqlright_mysql() {
  local work="$RUNTIME_DIR/sqlright-mysql"
  rm -rf "$work"
  mkdir -p "$work"
  cp -a "$SQLRIGHT_ROOT/MySQL/docker/fuzz_root/." "$work/"
  rm -rf "$work/inputs"
  mkdir -p "$work/inputs"
  printf '%s\n' "$work"
}

record_initial_queue_mtime() {
  local qdir="$1"
  local outfile="$LOG_DIR/queue_initial_mtime.tsv"
  (
    for _ in $(seq 1 300); do
      if [ -d "$qdir" ] && find "$qdir" -maxdepth 1 -type f -print -quit | grep -q .; then
        {
          printf 'name\tmtime_epoch\tmtime_iso\tsize\n'
          find "$qdir" -maxdepth 1 -type f -printf '%f\t%T@\t%TY-%Tm-%TdT%TH:%TM:%TS\t%s\n' | sort
        } > "$outfile.tmp"
        mv "$outfile.tmp" "$outfile"
        log "recorded initial queue mtime snapshot: $outfile"
        exit 0
      fi
      sleep 1
    done
    log "queue mtime snapshot skipped: queue not ready after timeout: $qdir"
  ) &
  QUEUE_MTIME_PID=$!
}

prepare_sqlright_inputs() {
  local keep_dir="$1"
  local input_dir="$2"
  local limit="${SQLRIGHT_INPUT_LIMIT:-all}"
  rm -rf "$input_dir"
  mkdir -p "$input_dir"
  if [ "$limit" = "all" ] || [ "$limit" = "0" ]; then
    find "$keep_dir" -maxdepth 1 -type f -size +0c -printf '%f\n' \
      | sort \
      | while IFS= read -r name; do
          cp -a "$keep_dir/$name" "$input_dir/$name"
        done
  else
    find "$keep_dir" -maxdepth 1 -type f -size +0c -printf '%f\n' \
      | sort \
      | sed -n "1,${limit}p" \
      | while IFS= read -r name; do
          cp -a "$keep_dir/$name" "$input_dir/$name"
        done
  fi
  local input_count
  input_count="$(find "$input_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  log "SQLRight inputs ready count=$input_count full_kept_dir=$keep_dir input_dir=$input_dir limit=$limit"
}

start_corpus_importer() {
  local corpus_dir="$1"
  local bootstrap_dir="$2"
  local queue_dir="$3"
  local total_count bootstrap_count
  total_count="$(find "$corpus_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  bootstrap_count="$(find "$bootstrap_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  if [ "$total_count" -le "$bootstrap_count" ]; then
    log "corpus importer skipped total_count=$total_count bootstrap_count=$bootstrap_count"
    return 0
  fi
  python3 "$STAGE_DIR/docker/common/import_corpus_to_queue.py"     --corpus-dir "$corpus_dir"     --bootstrap-dir "$bootstrap_dir"     --queue-dir "$queue_dir"     --log-file "$LOG_DIR/corpus_importer.log"     --import-manifest "$LOG_DIR/imported_corpus.tsv"     --queue-mtime-file "$LOG_DIR/queue_initial_mtime.tsv"     --wait-timeout "${CORPUS_IMPORT_WAIT_TIMEOUT:-300}" &
  CORPUS_IMPORT_PID=$!
  log "corpus importer pid=$CORPUS_IMPORT_PID corpus_dir=$corpus_dir queue=$queue_dir"
}

staged_corpus_enabled() {
  [ "${STAGED_CORPUS_ENABLED:-0}" = "1" ] || [ "${STAGED_CORPUS_ENABLED:-}" = "true" ]
}

start_staged_corpus_sync() {
  local corpus_dir="$1"
  local bootstrap_dir="$2"
  local queue_dir="$3"
  local sync_dir="$4"
  local total_count bootstrap_count
  total_count="$(find "$corpus_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  bootstrap_count="$(find "$bootstrap_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  if [ "$total_count" -le "$bootstrap_count" ]; then
    log "staged corpus sync skipped total_count=$total_count bootstrap_count=$bootstrap_count"
    return 0
  fi
  python3 "$STAGE_DIR/docker/common/staged_corpus_sync.py"     --corpus-dir "$corpus_dir"     --bootstrap-dir "$bootstrap_dir"     --queue-dir "$queue_dir"     --sync-dir "$sync_dir"     --sync-id "${STAGED_SYNC_ID:-sqleek_staged}"     --log-file "$LOG_DIR/staged_corpus_sync.log"     --import-manifest "$LOG_DIR/staged_corpus_imported.tsv"     --queue-mtime-file "$LOG_DIR/queue_initial_mtime.tsv"     --dry-run-log-dir "$LOG_DIR"     --plot-data "${queue_dir%/queue}/plot_data"     --energy-trace "${queue_dir%/queue}/sqleek_energy_trace.tsv"     --wait-timeout "${STAGED_QUEUE_WAIT_TIMEOUT:-300}"     --dry-run-timeout "${STAGED_DRY_RUN_TIMEOUT:-3600}"     --poll-interval "${STAGED_POLL_INTERVAL:-5}"     --batch-size "${STAGED_BATCH_SIZE:-100}"     --batch-interval "${STAGED_BATCH_INTERVAL:-60}"     --wait-for-dry-run &
  STAGED_IMPORT_PID=$!
  log "staged corpus sync pid=$STAGED_IMPORT_PID corpus_dir=$corpus_dir bootstrap_dir=$bootstrap_dir sync_dir=$sync_dir sync_id=${STAGED_SYNC_ID:-sqleek_staged}"
}

run_mysql_sqlright() {
  local keep_dir="$1"
  local sqlright_input_dir="$RUNTIME_DIR/seeds.sqlright_input"
  prepare_sqlright_inputs "$keep_dir" "$sqlright_input_dir"
  local sqlright_work
  sqlright_work="$(prepare_sqlright_mysql)"
  cp -a "$sqlright_input_dir/." "$sqlright_work/inputs/"
  cp "$SQLRIGHT_ROOT/MySQL/docker/src/afl-fuzz" "$sqlright_work/afl-fuzz"

  local fuzzer_dir="$RUN_OUTPUT/${DBMS}_memory/default"
  local afl_output_arg="$fuzzer_dir"
  local afl_sync_args=""
  if staged_corpus_enabled; then
    export SQLEEK_AFL_SYNC_ID="${SQLEEK_AFL_SYNC_ID:-default}"
    afl_output_arg="$RUN_OUTPUT/${DBMS}_memory"
    afl_sync_args="-S $SQLEEK_AFL_SYNC_ID"
  fi
  local fuzz_command="$sqlright_work/afl-fuzz -t $AFL_TIMEOUT -m $MEMORY_LIMIT -P ${SQLEEK_PORT_START:-9000} -K /tmp/mysql_${INSTANCE_ID}.sock -i $sqlright_work/inputs -o $afl_output_arg $afl_sync_args -c ${INSTANCE_ID} -O ${SQLRIGHT_ORACLE:-NOREC} aaa"
  start_fuzzer_stats_compat "$fuzzer_dir" "$fuzz_command"

  log "starting SQLRight MySQL adapter workdir=$sqlright_work output=$RUN_OUTPUT"
  (
    cd "$sqlright_work"
    export SQLEEK_DBMS="$DBMS"
    export SQLEEK_OUTPUT_LAYOUT=1
    export SQLEEK_INPUT_DIR="$sqlright_work/inputs"
    export SQLEEK_AFL_TIMEOUT="$AFL_TIMEOUT"
    export SQLEEK_MEMORY_LIMIT="$MEMORY_LIMIT"
    export SQLEEK_MYSQL_ROOT="/opt/dbms"
    export SQLEEK_START_CORE="${INSTANCE_ID}"
    export SQLEEK_NUM_CONCURRENT="${NUM_CONCURRENT:-1}"
    export SQLEEK_LOG_DIR="$LOG_DIR"
    export SQLEEK_ENERGY_FILE="${SQLEEK_ENERGY_FILE:-$RUN_OUTPUT/${DBMS}_memory/.deferred/energy.tsv}"
    export SQLEEK_ENERGY_TRACE="${SQLEEK_ENERGY_TRACE:-$RUN_OUTPUT/${DBMS}_memory/default/sqleek_energy_trace.tsv}"
    export SQLEEK_ENERGY_UNIT_MUTATIONS="${SQLEEK_ENERGY_UNIT_MUTATIONS:-25}"
    export SQLEEK_MUTATION_BUDGET_MAX="${SQLEEK_MUTATION_BUDGET_MAX:-1600}"
    export AFL_NO_AFFINITY="${AFL_NO_AFFINITY:-1}"
    export PYTHONUNBUFFERED=1
    export SQLEEK_AFL_SYNC_ID="${SQLEEK_AFL_SYNC_ID:-}"
    export LD_LIBRARY_PATH="$SQLRIGHT_ROOT/MySQL/docker/src/parser/mysql-server/bld/library_output_directory:${LD_LIBRARY_PATH:-}"
    timeout "$(duration_seconds "$DURATION")" \
      python3 run_parallel.py \
        -o "$RUN_OUTPUT" \
        --start-core "${INSTANCE_ID}" \
        --num-concurrent "${NUM_CONCURRENT:-1}" \
        --oracle "${SQLRIGHT_ORACLE:-NOREC}" \
        ${AFL_EXTRA_ARGS:-}
  ) >>"$SQLRIGHT_LOG" 2>&1 &
  SQLRIGHT_PID=$!
  record_initial_queue_mtime "$fuzzer_dir/queue"
  if staged_corpus_enabled; then
    start_staged_corpus_sync "$keep_dir" "$sqlright_input_dir" "$fuzzer_dir/queue" "$RUN_OUTPUT/${DBMS}_memory"
  fi
}


prepare_sqlright_postgresql() {
  local work="$RUNTIME_DIR/sqlright-postgresql"
  rm -rf "$work"
  mkdir -p "$work"
  cp -a "$SQLRIGHT_ROOT/PostgreSQL/docker/fuzz_root/." "$work/"
  rm -rf "$work/inputs"
  mkdir -p "$work/inputs"
  cp "$SQLRIGHT_ROOT/PostgreSQL/docker/src/afl-fuzz" "$work/afl-fuzz"
  chmod +x "$work/afl-fuzz"
  printf '%s\n' "$work"
}

run_postgresql_sqlright() {
  local keep_dir="$1"
  local sqlright_input_dir="$RUNTIME_DIR/seeds.sqlright_input"
  prepare_sqlright_inputs "$keep_dir" "$sqlright_input_dir"
  local sqlright_work
  sqlright_work="$(prepare_sqlright_postgresql)"
  cp -a "$sqlright_input_dir/." "$sqlright_work/inputs/"

  local fuzzer_dir="$RUN_OUTPUT/${DBMS}_memory/default"
  local afl_output_arg="$fuzzer_dir"
  local afl_sync_args=""
  if staged_corpus_enabled; then
    export SQLEEK_AFL_SYNC_ID="${SQLEEK_AFL_SYNC_ID:-default}"
    afl_output_arg="$RUN_OUTPUT/${DBMS}_memory"
    afl_sync_args="-S $SQLEEK_AFL_SYNC_ID"
  fi
  local fuzz_command="$sqlright_work/afl-fuzz -t $AFL_TIMEOUT -m $MEMORY_LIMIT -P ${SQLEEK_PORT_START:-7000} -i $sqlright_work/inputs -o $afl_output_arg $afl_sync_args -c ${INSTANCE_ID} -O ${SQLRIGHT_ORACLE:-NOREC} aaa"
  start_fuzzer_stats_compat "$fuzzer_dir" "$fuzz_command"

  log "starting SQLRight PostgreSQL adapter workdir=$sqlright_work output=$RUN_OUTPUT"
  (
    cd "$sqlright_work"
    export SQLEEK_DBMS="$DBMS"
    export SQLEEK_OUTPUT_LAYOUT=1
    export SQLEEK_INPUT_DIR="$sqlright_work/inputs"
    export SQLEEK_SQLRIGHT_AFL="$sqlright_work/afl-fuzz"
    export SQLEEK_AFL_TIMEOUT="$AFL_TIMEOUT"
    export SQLEEK_MEMORY_LIMIT="$MEMORY_LIMIT"
    export SQLEEK_POSTGRES_ROOT="/opt/dbms"
    export SQLEEK_POSTGRES_BIN="/opt/dbms/bin/postgres"
    export SQLEEK_POSTGRES_HOST="${SQLEEK_POSTGRES_HOST:-127.0.0.1}"
    export SQLEEK_POSTGRES_USER="${SQLEEK_POSTGRES_USER:-postgres}"
    export SQLEEK_PORT_START="${SQLEEK_PORT_START:-7000}"
    export SQLEEK_START_CORE="${INSTANCE_ID}"
    export SQLEEK_NUM_CONCURRENT="${NUM_CONCURRENT:-1}"
    export SQLEEK_LOG_DIR="$LOG_DIR"
    export SQLEEK_ENERGY_FILE="${SQLEEK_ENERGY_FILE:-$RUN_OUTPUT/${DBMS}_memory/.deferred/energy.tsv}"
    export SQLEEK_ENERGY_TRACE="${SQLEEK_ENERGY_TRACE:-$RUN_OUTPUT/${DBMS}_memory/default/sqleek_energy_trace.tsv}"
    export SQLEEK_ENERGY_UNIT_MUTATIONS="${SQLEEK_ENERGY_UNIT_MUTATIONS:-25}"
    export SQLEEK_MUTATION_BUDGET_MAX="${SQLEEK_MUTATION_BUDGET_MAX:-1600}"
    export SQLEEK_PG_DEFAULT_MUTATION_BUDGET="${SQLEEK_PG_DEFAULT_MUTATION_BUDGET:-100}"
    export SQLEEK_PG_STATEMENT_TIMEOUT_MS="${SQLEEK_PG_STATEMENT_TIMEOUT_MS:-200}"
    export SQLEEK_PG_CLIENT_TIMEOUT_MS="${SQLEEK_PG_CLIENT_TIMEOUT_MS:-2000}"
    export SQLEEK_PG_CONNECT_RETRIES="${SQLEEK_PG_CONNECT_RETRIES:-3}"
    export AFL_MAP_SIZE="${AFL_MAP_SIZE:-262144}"
    export AFL_NO_AFFINITY="${AFL_NO_AFFINITY:-1}"
    export PYTHONUNBUFFERED=1
    export LD_LIBRARY_PATH="/opt/dbms/lib:${LD_LIBRARY_PATH:-}"
    export SQLEEK_AFL_SYNC_ID="${SQLEEK_AFL_SYNC_ID:-}"
    timeout "$(duration_seconds "$DURATION")" \
      python3 run_parallel.py \
        -o "$RUN_OUTPUT" \
        --start-core "${INSTANCE_ID}" \
        --num-concurrent "${NUM_CONCURRENT:-1}" \
        --oracle "${SQLRIGHT_ORACLE:-NOREC}" \
        ${AFL_EXTRA_ARGS:-}
  ) >>"$SQLRIGHT_LOG" 2>&1 &
  SQLRIGHT_PID=$!
  record_initial_queue_mtime "$fuzzer_dir/queue"
  if staged_corpus_enabled; then
    start_staged_corpus_sync "$keep_dir" "$sqlright_input_dir" "$fuzzer_dir/queue" "$RUN_OUTPUT/${DBMS}_memory"
  fi
}


prepare_sqlright_monetdb() {
  local work="$RUNTIME_DIR/sqlright-monetdb"
  rm -rf "$work"
  mkdir -p "$work"
  cp -a "$SQLRIGHT_ROOT/SQLite/docker/fuzz_root/." "$work/"
  rm -rf "$work/inputs"
  mkdir -p "$work/inputs"
  cp "$SQLRIGHT_ROOT/SQLite/docker/src/afl-fuzz" "$work/afl-fuzz"
  chmod +x "$work/afl-fuzz"
  printf '%s\n' "$work"
}

run_monetdb_sqlright() {
  local keep_dir="$1"
  if [ "${SQLEEK_MONETDB_REQUIRE_SQLITE_COMPAT:-1}" = "1" ]; then
    local bad_seed=""
    local seed_file
    while IFS= read -r -d '' seed_file; do
      if ! grep -q '^-- adapter=monetdb_sqlite_parser_compat' "$seed_file"; then
        bad_seed="$seed_file"
        break
      fi
    done < <(find "$keep_dir" -maxdepth 1 -type f -size +0c -print0)
    if [ -n "$bad_seed" ]; then
      log "MonetDB seed validation failed: SQLRight uses the SQLite parser, but this seed is not parser-compatible: $bad_seed"
      log "Use experiment/RQ4/tools/monetdb_sqlite_seed_adapter.py and set SQLEEK_MONETDB_REQUIRE_SQLITE_COMPAT=0 only for diagnostics."
      return 2
    fi
  fi
  local sqlright_input_dir="$RUNTIME_DIR/seeds.sqlright_input"
  prepare_sqlright_inputs "$keep_dir" "$sqlright_input_dir"
  local sqlright_work
  sqlright_work="$(prepare_sqlright_monetdb)"
  cp -a "$sqlright_input_dir/." "$sqlright_work/inputs/"

  local fuzzer_dir="$RUN_OUTPUT/${DBMS}_memory/default"
  local afl_output_arg="$fuzzer_dir"
  local afl_sync_args=""
  if staged_corpus_enabled; then
    export SQLEEK_AFL_SYNC_ID="${SQLEEK_AFL_SYNC_ID:-default}"
    afl_output_arg="$RUN_OUTPUT/${DBMS}_memory"
    afl_sync_args="-S $SQLEEK_AFL_SYNC_ID"
  fi
  local monetdb_afl_timeout="${SQLEEK_MONETDB_AFL_TIMEOUT:-20000}"
  local target_wrapper="$STAGE_DIR/docker/common/monetdb_single_wrapper.sh"
  local fuzz_command="$sqlright_work/afl-fuzz -t ${monetdb_afl_timeout}+ -m $MEMORY_LIMIT -i $sqlright_work/inputs -o $afl_output_arg $afl_sync_args -c ${INSTANCE_ID} -O ${SQLRIGHT_ORACLE:-NOREC} ${AFL_EXTRA_ARGS:-} -- $target_wrapper @@"
  start_fuzzer_stats_compat "$fuzzer_dir" "$fuzz_command"

  log "starting SQLRight SQLite mutator with MonetDB target workdir=$sqlright_work output=$RUN_OUTPUT"
  (
    cd "$sqlright_work"
    export SQLEEK_DBMS="$DBMS"
    export SQLEEK_OUTPUT_LAYOUT=1
    export SQLEEK_INPUT_DIR="$sqlright_work/inputs"
    export SQLEEK_SQLRIGHT_AFL="$sqlright_work/afl-fuzz"
    export SQLEEK_AFL_TIMEOUT="$monetdb_afl_timeout"
    export SQLEEK_MEMORY_LIMIT="$MEMORY_LIMIT"
    export SQLEEK_MONETDB_ROOT="${SQLEEK_MONETDB_ROOT:-/opt/dbms}"
    export SQLEEK_MONETDB_MSERVER="${SQLEEK_MONETDB_MSERVER:-/opt/dbms/bin/mserver5}"
    export SQLEEK_MONETDB_MCLIENT="${SQLEEK_MONETDB_MCLIENT:-/opt/dbms/bin/mclient}"
    export SQLEEK_MONETDB_TMPDIR="${SQLEEK_MONETDB_TMPDIR:-$RUNTIME_DIR/monetdb/tmp}"
    export SQLEEK_MONETDB_DATADIR="${SQLEEK_MONETDB_DATADIR:-$RUNTIME_DIR/monetdb/data}"
    export SQLEEK_MONETDB_LOGDIR="${SQLEEK_MONETDB_LOGDIR:-$LOG_DIR/monetdb_server}"
    export SQLEEK_MONETDB_SEED_TIMEOUT="${SQLEEK_MONETDB_SEED_TIMEOUT:-8}"
    export SQLEEK_PORT_START="${SQLEEK_PORT_START:-43000}"
    export SQLEEK_LOG_DIR="$LOG_DIR"
    export SQLEEK_ENERGY_FILE="${SQLEEK_ENERGY_FILE:-$RUN_OUTPUT/${DBMS}_memory/.deferred/energy.tsv}"
    export SQLEEK_ENERGY_TRACE="${SQLEEK_ENERGY_TRACE:-$RUN_OUTPUT/${DBMS}_memory/default/sqleek_energy_trace.tsv}"
    export SQLEEK_ENERGY_UNIT_MUTATIONS="${SQLEEK_ENERGY_UNIT_MUTATIONS:-25}"
    export SQLEEK_MUTATION_BUDGET_MAX="${SQLEEK_MUTATION_BUDGET_MAX:-1600}"
    export SQLEEK_MONETDB_DEFAULT_MUTATION_BUDGET="${SQLEEK_MONETDB_DEFAULT_MUTATION_BUDGET:-100}"
    export SQLEEK_MONETDB_COVERAGE_ONLY="${SQLEEK_MONETDB_COVERAGE_ONLY:-1}"
    export SQLRIGHT_SYNC_COVERAGE_ONLY="${SQLRIGHT_SYNC_COVERAGE_ONLY:-1}"
    export SQLRIGHT_SYNC_KEEP_NONCOV="${SQLRIGHT_SYNC_KEEP_NONCOV:-1}"
    export AFL_MAP_SIZE="${AFL_MAP_SIZE:-262144}"
    export AFL_IGNORE_PROBLEMS="${AFL_IGNORE_PROBLEMS:-1}"
    export AFL_IGNORE_PROBLEMS_COVERAGE="${AFL_IGNORE_PROBLEMS_COVERAGE:-1}"
    export AFL_NO_FORKSRV="${AFL_NO_FORKSRV:-1}"
    export AFL_SKIP_BIN_CHECK="${AFL_SKIP_BIN_CHECK:-1}"
    export AFL_NO_AFFINITY="${AFL_NO_AFFINITY:-1}"
    export PYTHONUNBUFFERED=1
    export LD_LIBRARY_PATH="/opt/dbms/lib:/opt/dbms/lib/monetdb5:${LD_LIBRARY_PATH:-}"
    # SQLRight prints one progress line per queue cycle when stdout is not a
    # TTY. MonetDB's SQLite-parser compatibility mode can cycle quickly, so
    # retaining those lines makes sqlright.log grow without bound. Preserve
    # all diagnostics, but drop only the two uninformative progress lines and
    # keep the timeout/AFL exit status through the pipeline.
    set +e
    timeout "$(duration_seconds "$DURATION")" bash -lc "$fuzz_command" 2>&1 |
      sed -u -E '/Entering queue cycle|Fuzzing test case/d' >>"$SQLRIGHT_LOG"
    sqlright_rc=${PIPESTATUS[0]}
    set -e
    exit "$sqlright_rc"
  ) &
  SQLRIGHT_PID=$!
  record_initial_queue_mtime "$fuzzer_dir/queue"
  if staged_corpus_enabled; then
    start_staged_corpus_sync "$keep_dir" "$sqlright_input_dir" "$fuzzer_dir/queue" "$RUN_OUTPUT/${DBMS}_memory"
  fi
}

prefilter_seeds() {
  local keep_dir="$RUNTIME_DIR/seeds.keep"
  local deferred_dir="$RUN_OUTPUT/deferred_initial"
  local config_args=()
  if [ -n "${SCHEDULER_CONFIG:-}" ]; then
    config_args=(--config "$SCHEDULER_CONFIG")
  fi
  rm -rf "$keep_dir" "$deferred_dir"
  mkdir -p "$keep_dir" "$deferred_dir"
  TARGET_DIR="$TARGET_DIR" OUTPUT_DIR="$RUN_OUTPUT" LOG_DIR="$LOG_DIR" \
    python3 "$STAGE_DIR/seed_scheduler.py" \
      --mode prefilter \
      --dbms "$DBMS" \
      --seed-dir "$SEED_DIR" \
      --out-keep-dir "$keep_dir" \
      --out-deferred-dir "$deferred_dir" \
      "${config_args[@]}" >>"$LOG_DIR/prefilter.log" 2>&1
  printf '%s\n' "$keep_dir"
}

write_initial_energy_plan() {
  local keep_dir="$1"
  local config_args=()
  if [ -n "${SCHEDULER_CONFIG:-}" ]; then
    config_args=(--config "$SCHEDULER_CONFIG")
  fi
  mkdir -p "$RUN_OUTPUT/${DBMS}_memory/.deferred"
  TARGET_DIR="$TARGET_DIR" OUTPUT_DIR="$RUN_OUTPUT" LOG_DIR="$LOG_DIR" \
    python3 "$STAGE_DIR/seed_scheduler.py" \
      --mode energy-plan \
      --dbms "$DBMS" \
      --seed-dir "$keep_dir" \
      --log-file "$SCHED_LOG" \
      "${config_args[@]}"
  export SQLEEK_ENERGY_FILE="$RUN_OUTPUT/${DBMS}_memory/.deferred/energy.tsv"
  log "initial SQLeek energy plan ready file=$SQLEEK_ENERGY_FILE"
}

start_scheduler() {
  local seconds
  local config_args=()
  if [ -n "${SCHEDULER_CONFIG:-}" ]; then
    config_args=(--config "$SCHEDULER_CONFIG")
  fi
  seconds="$(duration_seconds "$DURATION")"
  TARGET_DIR="$TARGET_DIR" OUTPUT_DIR="$RUN_OUTPUT" LOG_DIR="$LOG_DIR" \
    python3 "$STAGE_DIR/seed_scheduler.py" \
      --mode online \
      --dbms "$DBMS" \
      --duration "$seconds" \
      --log-file "$SCHED_LOG" \
      "${config_args[@]}" &
  SCHED_PID=$!
  log "scheduler pid=$SCHED_PID"
}

smoke_checks() {
  local qdir="$RUN_OUTPUT/${DBMS}_memory/default/queue"
  local stats="$RUN_OUTPUT/${DBMS}_memory/default/fuzzer_stats"
  local plot="$RUN_OUTPUT/${DBMS}_memory/default/plot_data"
  local energy_trace="$RUN_OUTPUT/${DBMS}_memory/default/sqleek_energy_trace.tsv"
  [ -d "$qdir" ] || { log "smoke failed: queue missing: $qdir"; exit 3; }
  [ -s "$stats" ] || { log "smoke failed: fuzzer_stats missing/empty: $stats"; exit 3; }
  [ -s "$plot" ] || { log "smoke failed: plot_data missing/empty: $plot"; exit 3; }
  [ "$(wc -l < "$plot")" -ge 2 ] || { log "smoke failed: plot_data has no runtime rows: $plot"; exit 3; }
  [ -s "$energy_trace" ] || { log "smoke failed: SQLRight AFL energy trace missing/empty: $energy_trace"; exit 3; }
  [ -s "$SCHED_LOG" ] || { log "smoke failed: scheduler log missing/empty: $SCHED_LOG"; exit 3; }
  grep -E 'final=|assigned_energy=|energy_plan' "$SCHED_LOG" >/dev/null || {
    log "smoke failed: scheduler decisions not observed in log"; exit 3;
  }
}

main() {
  normalize_dbms_name
  log "mode=$MODE dbms=$DBMS run_id=$RUN_ID duration=$DURATION"
  INSTANCE_ID="$(detect_instance_id)"
  export INSTANCE_ID
  log "instance id selected=$INSTANCE_ID docker_cpuset=${CPUSET_CPUS:-not_set}"
  require_non_empty_seed_dir
  require_targets
  validate_scheduler_config
  require_runtime_tools

  if [ -e "$RUN_OUTPUT/.sqleek_running" ]; then
    log "output dir has an active marker: $RUN_OUTPUT"
    exit 2
  fi
  touch "$RUN_OUTPUT/.sqleek_running"

  local keep_dir
  keep_dir="$(prefilter_seeds)"
  local initial_corpus_count
  initial_corpus_count="$(find "$keep_dir" -maxdepth 1 -type f -size +0c | wc -l)"
  log "initial corpus ready seed_dir=$SEED_DIR kept_files=$initial_corpus_count keep_dir=$keep_dir"
  mkdir -p "$RUN_OUTPUT/${DBMS}_memory"
  local energy_seed_dir="$keep_dir"
  if staged_corpus_enabled; then
    local limit="${SQLRIGHT_INPUT_LIMIT:-all}"
    if [ "$limit" != "all" ] && [ "$limit" != "0" ]; then
      energy_seed_dir="$RUNTIME_DIR/seeds.energy_plan"
      prepare_sqlright_inputs "$keep_dir" "$energy_seed_dir"
      log "staged energy bootstrap active_dir=$energy_seed_dir full_kept_dir=$keep_dir limit=$limit"
    fi
  fi
  write_initial_energy_plan "$energy_seed_dir"
  start_scheduler

  case "$DBMS" in
    mysql|mariadb) run_mysql_sqlright "$keep_dir" ;;
    postgres) run_postgresql_sqlright "$keep_dir" ;;
    monetdb) run_monetdb_sqlright "$keep_dir" ;;
    *) log "SQLRight runtime adapter for $DBMS is not enabled in this image"; exit 4 ;;
  esac

  local sqlright_rc=0
  wait "$SQLRIGHT_PID" || sqlright_rc=$?
  stop_fuzzer_stats_compat
  if grep -q 'Segmentation fault' "$SQLRIGHT_LOG" 2>/dev/null; then
    log "SQLRight/AFL failed with SIGSEGV; see $SQLRIGHT_LOG"
    exit 139
  fi
  if [ "$sqlright_rc" -ne 0 ] && [ "$sqlright_rc" -ne 124 ]; then
    log "SQLRight/AFL exited with rc=$sqlright_rc; see $SQLRIGHT_LOG"
    exit "$sqlright_rc"
  fi
  if [ "$MODE" = "smoke" ]; then
    smoke_checks
  fi
  rm -f "$RUN_OUTPUT/.sqleek_running"
  log "run complete"
}

main "$@"
