#!/usr/bin/env bash
set -e

if [ $# -ne 1 ]; then
  echo "usage: run_rq2_24h.sh REPEAT_ID" >&2
  exit 2
fi

REPEAT_ID=$1
case "$REPEAT_ID" in
  r[0-9]*|R[0-9]*) ;;
  *)
    echo "repeat id should look like r1, r2, ..." >&2
    exit 2
    ;;
esac

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/sqlite}
DURATION_SECONDS=${RQ2_AFLGO_DURATION_SECONDS:-86400}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/sqlite/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2_24h}
HARNESS=${HARNESS:-$WORK_DIR/bin/sqlite_harness_aflgo}
FUZZER_NAME=sqlite_aflgo_${REPEAT_ID}

if [ ! -x "$AFLGO_DIR/afl-2.57b/afl-fuzz" ]; then
  echo "missing AFLGo fuzzer: $AFLGO_DIR/afl-2.57b/afl-fuzz" >&2
  exit 1
fi

if [ ! -x "$HARNESS" ]; then
  echo "missing harness: $HARNESS" >&2
  exit 1
fi

if [ ! -d "$SEED_DIR" ]; then
  echo "missing RQ2 SQLite seed directory: $SEED_DIR" >&2
  exit 1
fi

mkdir -p "$OUT_ROOT" "$WORK_DIR/logs"
RUN_ID=${FUZZER_NAME}_$(date -u +%Y%m%d_%H%M%S)
OUT_DIR=$OUT_ROOT/$RUN_ID
LOG_FILE=$WORK_DIR/logs/${RUN_ID}.log
SUMMARY=$WORK_DIR/logs/${RUN_ID}_summary.txt
MANIFEST=$WORK_DIR/logs/${RUN_ID}_manifest.txt
CMD_FILE=$WORK_DIR/logs/${RUN_ID}.command.sh

START_EPOCH=$(date -u +%s)
START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_UI=1
export AFL_NO_AFFINITY=1
export AFLGO_SQLITE_DB_LIMIT_MB=${AFLGO_SQLITE_DB_LIMIT_MB:-64}
export AFLGO_SQLITE_TMPDIR=${AFLGO_SQLITE_TMPDIR:-/tmp}

cat > "$CMD_FILE" <<EOF_CMD
cd "$AFLGO_DIR"
AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 AFL_NO_AFFINITY=1 AFLGO_SQLITE_DB_LIMIT_MB="$AFLGO_SQLITE_DB_LIMIT_MB" AFLGO_SQLITE_TMPDIR="$AFLGO_SQLITE_TMPDIR" \\
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \\
  -S "$FUZZER_NAME" \\
  -z exp \\
  -c 12h \\
  -m none \\
  -t 5000+ \\
  -i "$SEED_DIR" \\
  -o "$OUT_DIR" \\
  -- "$HARNESS" @@
EOF_CMD

{
  echo "run_id=$RUN_ID"
  echo "repeat_id=$REPEAT_ID"
  echo "start_time_utc=$START_ISO"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "aflgo_command_file=$CMD_FILE"
  echo "aflgo_commit=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "sqlite_commit=$(cd /root/SQLeek/sources/sqlite && git rev-parse HEAD 2>/dev/null || true)"
  echo "target_list=$WORK_DIR/targets/sqlite_rq2_targets.txt"
  echo "target_list_sha256=$(sha256sum "$WORK_DIR/targets/sqlite_rq2_targets.txt" | awk '{print $1}')"
  echo "seed_corpus=$SEED_DIR"
  echo "aflgo_sqlite_db_limit_mb=$AFLGO_SQLITE_DB_LIMIT_MB"
  echo "aflgo_sqlite_tmpdir=$AFLGO_SQLITE_TMPDIR"
  echo "out_dir=$OUT_DIR"
  echo "log_file=$LOG_FILE"
} > "$MANIFEST"

set +e
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \
  -S "$FUZZER_NAME" \
  -z exp \
  -c 12h \
  -m none \
  -t 5000+ \
  -i "$SEED_DIR" \
  -o "$OUT_DIR" \
  -- "$HARNESS" @@ >"$LOG_FILE" 2>&1
rc=$?
set -e

END_EPOCH=$(date -u +%s)
END_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RUN_TIME=$((END_EPOCH - START_EPOCH))
STATS=$OUT_DIR/$FUZZER_NAME/fuzzer_stats
PLOT=$OUT_DIR/$FUZZER_NAME/plot_data
QUEUE=$OUT_DIR/$FUZZER_NAME/queue
CRASHES=$OUT_DIR/$FUZZER_NAME/crashes
HANGS=$OUT_DIR/$FUZZER_NAME/hangs

{
  cat "$MANIFEST"
  echo "end_time_utc=$END_ISO"
  echo "exit_code=$rc"
  echo "run_time=$RUN_TIME"
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
  [ -d "$QUEUE" ] && echo "queue_files=$(find "$QUEUE" -type f | wc -l)" || echo "queue_files=missing"
  [ -d "$CRASHES" ] && echo "crash_files=$(find "$CRASHES" -type f ! -name README.txt | wc -l)" || echo "crash_files=missing"
  [ -d "$HANGS" ] && echo "hang_files=$(find "$HANGS" -type f | wc -l)" || echo "hang_files=missing"
  echo "queue_dir=$QUEUE"
  echo "crashes_dir=$CRASHES"
  echo "hangs_dir=$HANGS"
  echo "fuzzer_stats_path=$STATS"
  echo "plot_data_path=$PLOT"
  for path_name in "$STATS" "$PLOT" "$QUEUE" "$CRASHES" "$HANGS"; do
    if [ -e "$path_name" ]; then
      echo "artifact_exists=$path_name"
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
