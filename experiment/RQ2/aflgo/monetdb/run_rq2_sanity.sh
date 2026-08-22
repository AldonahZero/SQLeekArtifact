#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MONETDB_SRC=${MONETDB_SRC:-/root/SQLeek/sources/monetdb}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/monetdb}
DURATION_SECONDS=${1:-3600}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/monetdb/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2_sanity}
MSERVER=${MSERVER:-$WORK_DIR/bin/mserver5_aflgo}
MCLIENT=${MCLIENT:-$WORK_DIR/bin/mclient_aflgo}
WRAPPER=${WRAPPER:-$WORK_DIR/bin/monetdb_single_wrapper_aflgo}
RUN_ID=${RUN_ID:-monetsan_$(date -u +%m%d%H%M%S)}
TMPDIR_RUN=${TMPDIR_RUN:-$WORK_DIR/runtime/${RUN_ID}_tmp}
DATADIR_RUN=${DATADIR_RUN:-$WORK_DIR/runtime/${RUN_ID}_data}
LOGDIR_RUN=${LOGDIR_RUN:-$WORK_DIR/runtime/${RUN_ID}_logs}
LOG_DIR=$WORK_DIR/logs
OUT_DIR=$OUT_ROOT/$RUN_ID

for path in "$AFLGO_DIR/afl-2.57b/afl-fuzz" "$MSERVER" "$MCLIENT" "$WRAPPER" "$WORK_DIR/monetdb_single_wrapper.sh"; do
  [ -x "$path" ] || { echo "missing executable: $path" >&2; exit 1; }
done
[ -d "$SEED_DIR" ] || { echo "missing RQ2 MonetDB seed directory: $SEED_DIR" >&2; exit 1; }
mkdir -p "$OUT_DIR" "$LOG_DIR" "$TMPDIR_RUN" "$DATADIR_RUN" "$LOGDIR_RUN"

export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_UI=1
export AFL_NO_AFFINITY=1
export AFL_FAST_CAL=1
export AFLGO_MONETDB_ADAPTER="$WORK_DIR"
export AFLGO_MONETDB_MSERVER="$MSERVER"
export AFLGO_MONETDB_MCLIENT="$MCLIENT"
export AFLGO_MONETDB_TMPDIR="$TMPDIR_RUN"
export AFLGO_MONETDB_DATADIR="$DATADIR_RUN"
export AFLGO_MONETDB_LOGDIR="$LOGDIR_RUN"
export AFLGO_MONETDB_SEED_TIMEOUT=${AFLGO_MONETDB_SEED_TIMEOUT:-8}
export AFLGO_MONETDB_FILE_LIMIT_MB=${AFLGO_MONETDB_FILE_LIMIT_MB:-4}
export AFLGO_MONETDB_SCRIPT="$WORK_DIR/monetdb_single_wrapper.sh"
SMOKE_SEED=$(find "$SEED_DIR" -maxdepth 1 -type f | sort | head -n 1)
[ -n "$SMOKE_SEED" ] && timeout 40 "$WRAPPER" "$SMOKE_SEED" >"$LOG_DIR/${RUN_ID}_wrapper_smoke.log" 2>&1 || true

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
  echo "tmpdir=$TMPDIR_RUN"
  echo "datadir=$DATADIR_RUN"
  echo "logdir=$LOGDIR_RUN"
  echo "mserver5=$MSERVER"
  echo "mclient=$MCLIENT"
  echo "wrapper=$WRAPPER"
  echo "script_wrapper=$WORK_DIR/monetdb_single_wrapper.sh"
  echo "aflgo_commit=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "monetdb_commit=$(cd "$MONETDB_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  [ -s "$WORK_DIR/targets/monetdb_rq2_targets.txt" ] && echo "target_hash=$(sha256sum "$WORK_DIR/targets/monetdb_rq2_targets.txt" | awk '{print $1}')"
  [ -s "$WORK_DIR/tmp/distance/distance.cfg.txt" ] && echo "distance_file=$WORK_DIR/tmp/distance/distance.cfg.txt"
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
pkill -TERM -f "$DATADIR_RUN/" >/dev/null 2>&1 || true
sleep 1
pkill -KILL -f "$DATADIR_RUN/" >/dev/null 2>&1 || true
rm -rf "$TMPDIR_RUN" "$DATADIR_RUN"
STATS=$OUT_DIR/$RUN_ID/fuzzer_stats
QUEUE=$OUT_DIR/$RUN_ID/queue
CRASHES=$OUT_DIR/$RUN_ID/crashes
HANGS=$OUT_DIR/$RUN_ID/hangs
PLOT=$OUT_DIR/$RUN_ID/plot_data
{
  echo "output_dir=$OUT_DIR/$RUN_ID"
  [ -f "$STATS" ] && awk -F: '/^(run_time|execs_done|execs_per_sec|paths_total|unique_crashes|unique_hangs)/ {gsub(/^[ \t]+/,"",$2); printf "%s=%s\n",$1,$2}' "$STATS" || echo "fuzzer_stats=missing"
  echo "queue_files=$(find "$QUEUE" -maxdepth 1 -type f 2>/dev/null | wc -l)"
  echo "crash_files=$(find "$CRASHES" -maxdepth 1 -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "hang_files=$(find "$HANGS" -maxdepth 1 -type f ! -name README.txt 2>/dev/null | wc -l)"
  echo "fuzzer_stats=$STATS"
  echo "plot_data=$PLOT"
  echo "queue=$QUEUE"
  echo "crashes=$CRASHES"
  echo "hangs=$HANGS"
} | tee "$OUT_DIR/run_summary.txt"
case "$rc" in 0|124) exit 0 ;; *) exit "$rc" ;; esac
