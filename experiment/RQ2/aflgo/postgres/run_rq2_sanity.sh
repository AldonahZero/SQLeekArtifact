#!/usr/bin/env bash
set -e
AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/postgres}
BIN_DIR=$WORK_DIR/bin
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/postgres/memory}
DURATION_SECONDS=${1:-3600}
RUN_ID=${RUN_ID:-pgsan_$(date +%m%d%H%M%S)}
OUT_BASE=${OUT_BASE:-$WORK_DIR/output_rq2_sanity}
OUT_DIR=$OUT_BASE/$RUN_ID
DATADIR=$WORK_DIR/runtime/${RUN_ID}_datadir
mkdir -p "$OUT_DIR" "$WORK_DIR/logs" "$WORK_DIR/runtime"
"$WORK_DIR/init_postgres_datadir.sh" "$DATADIR" > "$WORK_DIR/logs/${RUN_ID}_initdb.path"
export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_AFFINITY=1
export AFL_NO_UI=1
export AFLGO_POSTGRES_BIN=$BIN_DIR/postgres_aflgo
export AFLGO_POSTGRES_DATADIR=$DATADIR
export AFLGO_POSTGRES_LOG=$WORK_DIR/logs/${RUN_ID}_postgres_single.err
export AFLGO_POSTGRES_SEED_TIMEOUT=5
export AFLGO_POSTGRES_FILE_LIMIT_MB=16
cmd=(timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" -S "$RUN_ID" -z exp -c 30m -m none -t 10000+ -i "$SEED_DIR" -o "$OUT_DIR" -- "$BIN_DIR/postgres_single_wrapper_aflgo" @@)
printf '%q ' "${cmd[@]}" > "$OUT_DIR/aflgo_command.txt"
echo >> "$OUT_DIR/aflgo_command.txt"
{
  echo "run_id=$RUN_ID"
  echo "start_time=$(date -Is)"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "seed_dir=$SEED_DIR"
  echo "datadir=$DATADIR"
  echo "aflgo_commit=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "postgres_commit=$(cd /root/SQLeek/sources/postgres && git rev-parse HEAD 2>/dev/null || true)"
  echo "target_hash=$(sha256sum "$WORK_DIR/targets/postgres_rq2_targets.txt" | awk '{print $1}')"
} > "$OUT_DIR/run_metadata.txt"
set +e
"${cmd[@]}"
rc=$?
set -e
{
  echo "end_time=$(date -Is)"
  echo "exit_code=$rc"
} >> "$OUT_DIR/run_metadata.txt"
stats=$OUT_DIR/$RUN_ID/fuzzer_stats
queue_dir=$OUT_DIR/$RUN_ID/queue
crash_dir=$OUT_DIR/$RUN_ID/crashes
hang_dir=$OUT_DIR/$RUN_ID/hangs
{
  echo "output_dir=$OUT_DIR/$RUN_ID"
  [ -f "$stats" ] && awk -F: '/^(execs_done|execs_per_sec|paths_total|unique_crashes|unique_hangs)[[:space:]]*:/ {gsub(/^[ \t]+/,"",$2); print $1"="$2}' "$stats"
  echo "queue_files=$(find "$queue_dir" -type f 2>/dev/null | wc -l)"
  echo "crash_files=$(find "$crash_dir" -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "hang_files=$(find "$hang_dir" -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "fuzzer_stats=$stats"
  echo "plot_data=$OUT_DIR/$RUN_ID/plot_data"
} | tee "$OUT_DIR/run_summary.txt"
exit "$rc"
