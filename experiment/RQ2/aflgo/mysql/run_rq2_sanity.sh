#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MYSQL_SRC=${MYSQL_SRC:-/root/SQLeek/sources/mysql}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mysql}
DURATION_SECONDS=${1:-3600}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/mysql/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2}
MYSQLD=${MYSQLD:-$WORK_DIR/bin/mysqld_aflgo}
WRAPPER=${WRAPPER:-$WORK_DIR/bin/mysql_bootstrap_wrapper_aflgo}
DATADIR=${DATADIR:-$WORK_DIR/runtime/mysql_sanity_datadir}
TMPDIR_RUN=${TMPDIR_RUN:-$WORK_DIR/runtime/tmp}
LOG_DIR=$WORK_DIR/logs

if [ ! -x "$AFLGO_DIR/afl-2.57b/afl-fuzz" ]; then
  echo "missing AFLGo fuzzer: $AFLGO_DIR/afl-2.57b/afl-fuzz" >&2
  exit 1
fi

if [ ! -x "$MYSQLD" ]; then
  echo "missing instrumented mysqld: $MYSQLD" >&2
  exit 1
fi

if [ ! -x "$WRAPPER" ]; then
  echo "missing bootstrap wrapper: $WRAPPER" >&2
  exit 1
fi

if [ ! -d "$SEED_DIR" ]; then
  echo "missing RQ2 MySQL seed directory: $SEED_DIR" >&2
  exit 1
fi

mkdir -p "$OUT_ROOT" "$LOG_DIR" "$WORK_DIR/runtime" "$TMPDIR_RUN"
RUN_ID=rq2_sanity_$(date -u +%Y%m%d_%H%M%S)
OUT_DIR=$OUT_ROOT/$RUN_ID
LOG_FILE=$LOG_DIR/${RUN_ID}.log
INIT_LOG=$LOG_DIR/${RUN_ID}_datadir_init.log
SUMMARY=$LOG_DIR/${RUN_ID}_summary.txt
CMD_FILE=$LOG_DIR/${RUN_ID}.command.sh
SMOKE_SEED=$(find "$SEED_DIR" -maxdepth 1 -type f | sort | head -n 1)

rm -rf "$DATADIR"
mkdir -p "$DATADIR" "$TMPDIR_RUN"
"$MYSQLD" \
  --no-defaults \
  --initialize-insecure \
  --datadir="$DATADIR" \
  --basedir="$MYSQL_SRC" \
  --character-sets-dir="$MYSQL_SRC/share/charsets" \
  --log-error="$INIT_LOG" \
  --tmpdir="$TMPDIR_RUN" \
  >>"$INIT_LOG" 2>&1

export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_UI=1
export AFL_NO_AFFINITY=1
export AFLGO_MYSQLD="$MYSQLD"
export AFLGO_MYSQL_DATADIR="$DATADIR"
export AFLGO_MYSQL_BASEDIR="$MYSQL_SRC"
export AFLGO_MYSQL_CHARSET_DIR="$MYSQL_SRC/share/charsets"
export AFLGO_MYSQL_TMPDIR="$TMPDIR_RUN"
export AFLGO_MYSQL_LOG_ERROR="$LOG_DIR/${RUN_ID}_mysqld.err"
export AFLGO_MYSQL_SEED_TIMEOUT=${AFLGO_MYSQL_SEED_TIMEOUT:-8}
export AFLGO_MYSQL_FILE_LIMIT_MB=${AFLGO_MYSQL_FILE_LIMIT_MB:-128}

if [ -n "$SMOKE_SEED" ]; then
  timeout 30 "$WRAPPER" "$SMOKE_SEED" >>"$LOG_DIR/${RUN_ID}_wrapper_smoke.log" 2>&1 || true
fi

cat > "$CMD_FILE" <<EOF_CMD
cd "$AFLGO_DIR"
AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 AFL_NO_AFFINITY=1 \\
AFLGO_MYSQLD="$MYSQLD" AFLGO_MYSQL_DATADIR="$DATADIR" AFLGO_MYSQL_BASEDIR="$MYSQL_SRC" \\
AFLGO_MYSQL_CHARSET_DIR="$MYSQL_SRC/share/charsets" AFLGO_MYSQL_TMPDIR="$TMPDIR_RUN" \\
AFLGO_MYSQL_LOG_ERROR="$LOG_DIR/${RUN_ID}_mysqld.err" AFLGO_MYSQL_SEED_TIMEOUT="$AFLGO_MYSQL_SEED_TIMEOUT" \\
AFLGO_MYSQL_FILE_LIMIT_MB="$AFLGO_MYSQL_FILE_LIMIT_MB" \\
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \\
  -S mysql_aflgo_rq2_sanity \\
  -z exp \\
  -c 30m \\
  -m none \\
  -t 20000+ \\
  -i "$SEED_DIR" \\
  -o "$OUT_DIR" \\
  -- "$WRAPPER" @@
EOF_CMD

set +e
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \
  -S mysql_aflgo_rq2_sanity \
  -z exp \
  -c 30m \
  -m none \
  -t 20000+ \
  -i "$SEED_DIR" \
  -o "$OUT_DIR" \
  -- "$WRAPPER" @@ >"$LOG_FILE" 2>&1
rc=$?
set -e

STATS=$OUT_DIR/mysql_aflgo_rq2_sanity/fuzzer_stats
QUEUE=$OUT_DIR/mysql_aflgo_rq2_sanity/queue
CRASHES=$OUT_DIR/mysql_aflgo_rq2_sanity/crashes
HANGS=$OUT_DIR/mysql_aflgo_rq2_sanity/hangs
PLOT=$OUT_DIR/mysql_aflgo_rq2_sanity/plot_data

{
  echo "run_id=$RUN_ID"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "exit_code=$rc"
  echo "seed_dir=$SEED_DIR"
  echo "seed_count=$(find "$SEED_DIR" -maxdepth 1 -type f | wc -l)"
  echo "out_dir=$OUT_DIR"
  echo "log_file=$LOG_FILE"
  echo "init_log=$INIT_LOG"
  echo "command_file=$CMD_FILE"
  echo "mysqld=$MYSQLD"
  echo "wrapper=$WRAPPER"
  echo "datadir=$DATADIR"
  echo "tmpdir=$TMPDIR_RUN"
  echo "aflgo_mysql_seed_timeout=$AFLGO_MYSQL_SEED_TIMEOUT"
  echo "aflgo_mysql_file_limit_mb=$AFLGO_MYSQL_FILE_LIMIT_MB"
  echo "mysql_commit=$(cd "$MYSQL_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_commit=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "target_list=$WORK_DIR/targets/mysql_rq2_targets.txt"
  [ -s "$WORK_DIR/targets/mysql_rq2_targets.txt" ] && echo "target_list_sha256=$(sha256sum "$WORK_DIR/targets/mysql_rq2_targets.txt" | awk '{print $1}')"
  if [ -f "$STATS" ]; then
    awk -F: '
      BEGIN { wanted["execs_per_sec"]; wanted["paths_total"]; wanted["unique_crashes"]; wanted["unique_hangs"]; wanted["execs_done"]; wanted["cycles_done"] }
      {
        key=$1
        val=$2
        gsub(/^[ \t]+|[ \t]+$/, "", key)
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        if (key in wanted) print key "=" val
      }
    ' "$STATS"
  else
    echo "fuzzer_stats=missing"
  fi
  [ -d "$QUEUE" ] && echo "queue_files=$(find "$QUEUE" -maxdepth 1 -type f | wc -l)" || echo "queue_files=missing"
  [ -d "$CRASHES" ] && echo "crash_files=$(find "$CRASHES" -maxdepth 1 -type f ! -name README.txt | wc -l)" || echo "crash_files=missing"
  [ -d "$HANGS" ] && echo "hang_files=$(find "$HANGS" -maxdepth 1 -type f ! -name README.txt | wc -l)" || echo "hang_files=missing"
  for name in "$STATS" "$PLOT" "$QUEUE" "$CRASHES" "$HANGS"; do
    if [ -e "$name" ]; then
      echo "artifact_exists=$name"
    fi
  done
} | tee "$SUMMARY"

case "$rc" in
  0|124)
    exit 0
    ;;
  *)
    exit "$rc"
    ;;
esac
