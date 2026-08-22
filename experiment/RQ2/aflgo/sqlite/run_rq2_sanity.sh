#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/sqlite}
DURATION_SECONDS=${1:-3600}
SEED_DIR=${SEED_DIR:-/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/sqlite/memory}
OUT_ROOT=${OUT_ROOT:-$WORK_DIR/output_rq2}
HARNESS=${HARNESS:-$WORK_DIR/bin/sqlite_harness_aflgo}

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
RUN_ID=rq2_sanity_$(date -u +%Y%m%d_%H%M%S)
OUT_DIR=$OUT_ROOT/$RUN_ID
LOG_FILE=$WORK_DIR/logs/${RUN_ID}.log

export AFL_SKIP_CPUFREQ=1
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1
export AFL_NO_UI=1
export AFL_NO_AFFINITY=1

set +e
timeout "$DURATION_SECONDS" "$AFLGO_DIR/afl-2.57b/afl-fuzz" \
  -S sqlite_aflgo_rq2_sanity \
  -z exp \
  -c 30m \
  -m none \
  -t 5000+ \
  -i "$SEED_DIR" \
  -o "$OUT_DIR" \
  -- "$HARNESS" @@ >"$LOG_FILE" 2>&1
rc=$?
set -e

STATS=$OUT_DIR/sqlite_aflgo_rq2_sanity/fuzzer_stats
SUMMARY=$WORK_DIR/logs/${RUN_ID}_summary.txt

{
  echo "run_id=$RUN_ID"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "exit_code=$rc"
  echo "seed_dir=$SEED_DIR"
  echo "out_dir=$OUT_DIR"
  echo "log_file=$LOG_FILE"
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
  if [ -d "$OUT_DIR/sqlite_aflgo_rq2_sanity/queue" ]; then
    echo "queue_files=$(find "$OUT_DIR/sqlite_aflgo_rq2_sanity/queue" -type f | wc -l)"
  fi
  if [ -d "$OUT_DIR/sqlite_aflgo_rq2_sanity/crashes" ]; then
    echo "crash_files=$(find "$OUT_DIR/sqlite_aflgo_rq2_sanity/crashes" -type f ! -name README.txt | wc -l)"
  fi
  if [ -d "$OUT_DIR/sqlite_aflgo_rq2_sanity/hangs" ]; then
    echo "hang_files=$(find "$OUT_DIR/sqlite_aflgo_rq2_sanity/hangs" -type f | wc -l)"
  fi
  for name in fuzzer_stats plot_data queue crashes hangs; do
    if [ -e "$OUT_DIR/sqlite_aflgo_rq2_sanity/$name" ]; then
      echo "${name}_exists=yes"
    else
      echo "${name}_exists=no"
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
