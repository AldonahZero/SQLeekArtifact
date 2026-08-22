#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MYSQL_SRC=${MYSQL_SRC:-/root/SQLeek/sources/mysql}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mysql}
REPEAT_ID=${1:-r1}
DURATION_SECONDS=${2:-86400}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/mysql/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2_24h}
MYSQLD=${MYSQLD:-$WORK_DIR/bin/mysqld_aflgo}
WRAPPER=${WRAPPER:-$WORK_DIR/bin/mysql_bootstrap_wrapper_aflgo}
TARGET_LIST=${TARGET_LIST:-$WORK_DIR/targets/mysql_rq2_targets.txt}
DISTANCE_FILE=${DISTANCE_FILE:-$WORK_DIR/tmp/distance/distance.cfg.txt}
LOG_ROOT=${LOG_ROOT:-$WORK_DIR/logs}

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
if [ ! -s "$TARGET_LIST" ]; then
  echo "missing target list: $TARGET_LIST" >&2
  exit 1
fi

case "$REPEAT_ID" in
  *[!A-Za-z0-9_-]*|'')
    echo "invalid repeat id: $REPEAT_ID" >&2
    exit 1
    ;;
esac

mkdir -p "$OUT_ROOT" "$LOG_ROOT"
RUN_ID=mysql_aflgo_${REPEAT_ID}_$(date -u +%Y%m%d_%H%M%S)
RUN_DIR=$OUT_ROOT/$RUN_ID
AFL_INSTANCE=mysql_aflgo_${REPEAT_ID}
AFL_OUT_DIR=$RUN_DIR/$AFL_INSTANCE
RUNTIME_DIR=$RUN_DIR/runtime
DATADIR=$RUNTIME_DIR/datadir
TMPDIR_RUN=$RUNTIME_DIR/tmp
RUN_LOG_DIR=$RUN_DIR/logs
LOG_FILE=$RUN_LOG_DIR/aflgo.log
INIT_LOG=$RUN_LOG_DIR/datadir_init.log
WRAPPER_SMOKE_LOG=$RUN_LOG_DIR/wrapper_smoke.log
MYSQLD_LOG=$RUN_LOG_DIR/mysqld.err
METADATA=$RUN_DIR/metadata.txt
SUMMARY=$RUN_DIR/summary.txt
CMD_FILE=$RUN_DIR/command.sh
SMOKE_SEED=$(find "$SEED_DIR" -maxdepth 1 -type f | sort | head -n 1)

if [ -e "$RUN_DIR" ]; then
  echo "run directory already exists: $RUN_DIR" >&2
  exit 1
fi
mkdir -p "$DATADIR" "$TMPDIR_RUN" "$RUN_LOG_DIR"

START_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MYSQL_COMMIT=$(cd "$MYSQL_SRC" && git rev-parse HEAD 2>/dev/null || true)
AFLGO_COMMIT=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)
TARGET_COUNT=$(wc -l < "$TARGET_LIST" | tr -d ' ')
TARGET_HASH=$(sha256sum "$TARGET_LIST" | awk '{print $1}')
SEED_COUNT=$(find "$SEED_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')
DISTANCE_COUNT=missing
if [ -s "$DISTANCE_FILE" ]; then
  DISTANCE_COUNT=$(wc -l < "$DISTANCE_FILE" | tr -d ' ')
fi

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
export AFLGO_MYSQL_LOG_ERROR="$MYSQLD_LOG"
export AFLGO_MYSQL_SEED_TIMEOUT=${AFLGO_MYSQL_SEED_TIMEOUT:-8}
export AFLGO_MYSQL_FILE_LIMIT_MB=${AFLGO_MYSQL_FILE_LIMIT_MB:-128}

if [ -n "$SMOKE_SEED" ]; then
  timeout 30 "$WRAPPER" "$SMOKE_SEED" >>"$WRAPPER_SMOKE_LOG" 2>&1 || true
fi

cat > "$CMD_FILE" <<EOF_CMD
cd "$AFLGO_DIR"
AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 AFL_NO_AFFINITY=1 \\
AFLGO_MYSQLD="$MYSQLD" AFLGO_MYSQL_DATADIR="$DATADIR" AFLGO_MYSQL_BASEDIR="$MYSQL_SRC" \\
AFLGO_MYSQL_CHARSET_DIR="$MYSQL_SRC/share/charsets" AFLGO_MYSQL_TMPDIR="$TMPDIR_RUN" \\
AFLGO_MYSQL_LOG_ERROR="$MYSQLD_LOG" AFLGO_MYSQL_SEED_TIMEOUT="$AFLGO_MYSQL_SEED_TIMEOUT" \\
AFLGO_MYSQL_FILE_LIMIT_MB="$AFLGO_MYSQL_FILE_LIMIT_MB" \\
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \\
  -S "$AFL_INSTANCE" \\
  -z exp \\
  -c 12h \\
  -m none \\
  -t 20000+ \\
  -i "$SEED_DIR" \\
  -o "$RUN_DIR" \\
  -- "$WRAPPER" @@
EOF_CMD
chmod +x "$CMD_FILE"

{
  echo "run_id=$RUN_ID"
  echo "repeat_id=$REPEAT_ID"
  echo "afl_instance=$AFL_INSTANCE"
  echo "start_time_utc=$START_TIME_UTC"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "seed_dir=$SEED_DIR"
  echo "seed_count=$SEED_COUNT"
  echo "run_dir=$RUN_DIR"
  echo "afl_out_dir=$AFL_OUT_DIR"
  echo "log_file=$LOG_FILE"
  echo "init_log=$INIT_LOG"
  echo "mysqld_log=$MYSQLD_LOG"
  echo "command_file=$CMD_FILE"
  echo "mysqld=$MYSQLD"
  echo "wrapper=$WRAPPER"
  echo "datadir=$DATADIR"
  echo "tmpdir=$TMPDIR_RUN"
  echo "mysql_commit=$MYSQL_COMMIT"
  echo "aflgo_commit=$AFLGO_COMMIT"
  echo "target_list=$TARGET_LIST"
  echo "target_count=$TARGET_COUNT"
  echo "target_list_sha256=$TARGET_HASH"
  echo "distance_file=$DISTANCE_FILE"
  echo "distance_count=$DISTANCE_COUNT"
} | tee "$METADATA"

set +e
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \
  -S "$AFL_INSTANCE" \
  -z exp \
  -c 12h \
  -m none \
  -t 20000+ \
  -i "$SEED_DIR" \
  -o "$RUN_DIR" \
  -- "$WRAPPER" @@ >"$LOG_FILE" 2>&1
rc=$?
set -e

END_TIME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STATS=$AFL_OUT_DIR/fuzzer_stats
QUEUE=$AFL_OUT_DIR/queue
CRASHES=$AFL_OUT_DIR/crashes
HANGS=$AFL_OUT_DIR/hangs
PLOT=$AFL_OUT_DIR/plot_data

{
  cat "$METADATA"
  echo "end_time_utc=$END_TIME_UTC"
  echo "exit_code=$rc"
  if [ -f "$STATS" ]; then
    awk -F: '
      BEGIN { wanted["run_time"]; wanted["execs_done"]; wanted["execs_per_sec"]; wanted["paths_total"]; wanted["unique_crashes"]; wanted["unique_hangs"] }
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
  [ -d "$QUEUE" ] && echo "queue_files=$(find "$QUEUE" -maxdepth 1 -type f | wc -l | tr -d ' ')" || echo "queue_files=missing"
  [ -d "$CRASHES" ] && echo "crash_files=$(find "$CRASHES" -maxdepth 1 -type f ! -name README.txt | wc -l | tr -d ' ')" || echo "crash_files=missing"
  [ -d "$HANGS" ] && echo "hang_files=$(find "$HANGS" -maxdepth 1 -type f ! -name README.txt | wc -l | tr -d ' ')" || echo "hang_files=missing"
  echo "queue_dir=$QUEUE"
  echo "crashes_dir=$CRASHES"
  echo "hangs_dir=$HANGS"
  echo "fuzzer_stats=$STATS"
  echo "plot_data=$PLOT"
  for artifact in "$QUEUE" "$CRASHES" "$HANGS" "$STATS" "$PLOT"; do
    if [ -e "$artifact" ]; then
      echo "artifact_exists=$artifact"
    else
      echo "artifact_missing=$artifact"
    fi
  done
} | tee "$SUMMARY"

cat >> "$METADATA" <<EOF_META
end_time_utc=$END_TIME_UTC
exit_code=$rc
summary=$SUMMARY
EOF_META

case "$rc" in
  0|124)
    exit 0
    ;;
  *)
    exit "$rc"
    ;;
esac
