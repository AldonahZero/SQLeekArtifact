#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MARIADB_SRC=${MARIADB_SRC:-/root/SQLeek/sources/mariadb}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mariadb}
DURATION_SECONDS=${1:-3600}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/mariadb/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2_sanity}
MARIADBD=${MARIADBD:-$WORK_DIR/bin/mariadbd_aflgo}
WRAPPER=${WRAPPER:-$WORK_DIR/bin/mariadb_bootstrap_wrapper_aflgo}
INSTALL_DB=${INSTALL_DB:-$WORK_DIR/bin/mariadb_install_db}
BUILD_DIR=${BUILD_DIR:-$WORK_DIR/build/mariadb-instrumented}
BASEDIR=${BASEDIR:-$WORK_DIR/runtime/mariadb_basedir}
RUN_ID=${RUN_ID:-mdbsan_$(date -u +%m%d%H%M%S)}
DATADIR=${DATADIR:-$WORK_DIR/runtime/${RUN_ID}_datadir}
TMPDIR_RUN=${TMPDIR_RUN:-$WORK_DIR/runtime/${RUN_ID}_tmp}
LOG_DIR=$WORK_DIR/logs
OUT_DIR=$OUT_ROOT/$RUN_ID


prepare_mariadb_basedir() {
  rm -rf "$BASEDIR"
  mkdir -p "$BASEDIR/bin" "$BASEDIR/extra" "$BASEDIR/sql" "$BASEDIR/share"
  ln -sf "$MARIADBD" "$BASEDIR/bin/mariadbd"
  ln -sf "$MARIADBD" "$BASEDIR/sql/mariadbd"
  if [ -x "$BUILD_DIR/extra/my_print_defaults" ]; then
    ln -sf "$BUILD_DIR/extra/my_print_defaults" "$BASEDIR/bin/my_print_defaults"
    ln -sf "$BUILD_DIR/extra/my_print_defaults" "$BASEDIR/extra/my_print_defaults"
  fi
  for f in "$BUILD_DIR"/scripts/*.sql "$MARIADB_SRC"/scripts/*.sql; do
    [ -f "$f" ] && ln -sf "$f" "$BASEDIR/share/$(basename "$f")"
  done
  ln -sfn "$MARIADB_SRC/share/charsets" "$BASEDIR/share/charsets"
  if [ -d "$BUILD_DIR/sql/share" ]; then
    for d in "$BUILD_DIR"/sql/share/*; do
      [ -d "$d" ] && ln -sfn "$d" "$BASEDIR/share/$(basename "$d")"
    done
  fi
  [ -x "$BASEDIR/bin/my_print_defaults" ] || { echo "missing my_print_defaults in $BASEDIR" >&2; exit 1; }
  [ -f "$BASEDIR/share/fill_help_tables.sql" ] || { echo "missing fill_help_tables.sql in $BASEDIR" >&2; exit 1; }
  [ -f "$BASEDIR/share/english/errmsg.sys" ] || { echo "missing errmsg.sys in $BASEDIR" >&2; exit 1; }
}

for path in "$AFLGO_DIR/afl-2.57b/afl-fuzz" "$MARIADBD" "$WRAPPER"; do
  [ -x "$path" ] || { echo "missing executable: $path" >&2; exit 1; }
done
[ -d "$SEED_DIR" ] || { echo "missing RQ2 MariaDB seed directory: $SEED_DIR" >&2; exit 1; }
mkdir -p "$OUT_DIR" "$LOG_DIR" "$WORK_DIR/runtime"
rm -rf "$TMPDIR_RUN"
mkdir -p "$TMPDIR_RUN"
prepare_mariadb_basedir
rm -rf "$DATADIR"
mkdir -p "$DATADIR"
INIT_LOG=$LOG_DIR/${RUN_ID}_datadir_init.log
if [ -x "$INSTALL_DB" ]; then
  "$INSTALL_DB" --no-defaults --basedir="$BASEDIR" --datadir="$DATADIR" --auth-root-authentication-method=normal --skip-test-db --force --user=root >"$INIT_LOG" 2>&1 || true
fi
if [ ! -d "$DATADIR/mysql" ]; then
  "$MARIADBD" --no-defaults --bootstrap --datadir="$DATADIR" --basedir="$BASEDIR" --character-sets-dir="$BASEDIR/share/charsets" --skip-grant-tables < /dev/null >>"$INIT_LOG" 2>&1 || true
fi

export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_UI=1
export AFL_NO_AFFINITY=1
export AFLGO_MARIADBD="$MARIADBD"
export AFLGO_MARIADB_DATADIR="$DATADIR"
export AFLGO_MARIADB_BASEDIR="$BASEDIR"
export AFLGO_MARIADB_CHARSET_DIR="$BASEDIR/share/charsets"
export AFLGO_MARIADB_TMPDIR="$TMPDIR_RUN"
export AFLGO_MARIADB_LOG_ERROR="$LOG_DIR/${RUN_ID}_mariadbd.err"
export AFLGO_MARIADB_SEED_TIMEOUT=${AFLGO_MARIADB_SEED_TIMEOUT:-8}
export AFLGO_MARIADB_FILE_LIMIT_MB=${AFLGO_MARIADB_FILE_LIMIT_MB:-128}
SMOKE_SEED=$(find "$SEED_DIR" -maxdepth 1 -type f | sort | head -n 1)
[ -n "$SMOKE_SEED" ] && timeout 30 "$WRAPPER" "$SMOKE_SEED" >"$LOG_DIR/${RUN_ID}_wrapper_smoke.log" 2>&1 || true

CMD_FILE=$OUT_DIR/aflgo_command.txt
cat > "$CMD_FILE" <<EOF_CMD
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" -S "$RUN_ID" -z exp -c 30m -m none -t 20000+ -i "$SEED_DIR" -o "$OUT_DIR" -- "$WRAPPER" @@
EOF_CMD
{
  echo "run_id=$RUN_ID"
  echo "start_time=$(date -Is)"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "seed_dir=$SEED_DIR"
  echo "seed_count=$(find "$SEED_DIR" -maxdepth 1 -type f | wc -l)"
  echo "datadir=$DATADIR"
  echo "basedir=$BASEDIR"
  echo "tmpdir=$TMPDIR_RUN"
  echo "mariadbd=$MARIADBD"
  echo "wrapper=$WRAPPER"
  echo "aflgo_commit=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "mariadb_commit=$(cd "$MARIADB_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  [ -s "$WORK_DIR/targets/mariadb_rq2_targets.txt" ] && echo "target_hash=$(sha256sum "$WORK_DIR/targets/mariadb_rq2_targets.txt" | awk '{print $1}')"
} > "$OUT_DIR/run_metadata.txt"

set +e
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \
  -S "$RUN_ID" -z exp -c 30m -m none -t 20000+ \
  -i "$SEED_DIR" -o "$OUT_DIR" -- "$WRAPPER" @@ >"$OUT_DIR/aflgo.log" 2>&1
rc=$?
set -e
{
  echo "end_time=$(date -Is)"
  echo "exit_code=$rc"
} >> "$OUT_DIR/run_metadata.txt"
find "$TMPDIR_RUN" -maxdepth 1 -type f -name "aflgo_mariadb_*" -delete 2>/dev/null || true
STATS=$OUT_DIR/$RUN_ID/fuzzer_stats
QUEUE=$OUT_DIR/$RUN_ID/queue
CRASHES=$OUT_DIR/$RUN_ID/crashes
HANGS=$OUT_DIR/$RUN_ID/hangs
PLOT=$OUT_DIR/$RUN_ID/plot_data
{
  echo "output_dir=$OUT_DIR/$RUN_ID"
  [ -f "$STATS" ] && awk -F: '/^(run_time|execs_done|execs_per_sec|paths_total|unique_crashes|unique_hangs)/ {gsub(/^[ \t]+/,"",$2); print $1"="$2}' "$STATS" || echo "fuzzer_stats=missing"
  echo "queue_files=$(find "$QUEUE" -maxdepth 1 -type f 2>/dev/null | wc -l)"
  echo "crash_files=$(find "$CRASHES" -maxdepth 1 -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "hang_files=$(find "$HANGS" -maxdepth 1 -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "fuzzer_stats=$STATS"
  echo "plot_data=$PLOT"
} | tee "$OUT_DIR/run_summary.txt"
case "$rc" in 0|124) exit 0 ;; *) exit "$rc" ;; esac
