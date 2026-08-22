#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/sqlite}
OUT_MD=$WORK_DIR/logs/sqlite_rq2_24h_summary.md

extract_value() {
  local key=$1 file=$2
  awk -F= -v k="$key" '$1 == k { print substr($0, index($0, "=") + 1); found=1 } END { if (!found) print "" }' "$file"
}

{
  echo "# SQLite AFLGo RQ2 24h Summary"
  echo
  echo "| repeat | run_time | execs_done | execs_per_sec | paths_total | queue_files | unique_crashes | unique_hangs | crash_files | hang_files | queue_dir | crashes_dir | hangs_dir | fuzzer_stats | plot_data |"
  echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|"
  for summary in "$WORK_DIR"/logs/sqlite_aflgo_r*_20????????_??????_summary.txt; do
    [ -f "$summary" ] || continue
    repeat=$(extract_value repeat_id "$summary")
    echo "| $repeat | $(extract_value run_time "$summary") | $(extract_value execs_done "$summary") | $(extract_value execs_per_sec "$summary") | $(extract_value paths_total "$summary") | $(extract_value queue_files "$summary") | $(extract_value unique_crashes "$summary") | $(extract_value unique_hangs "$summary") | $(extract_value crash_files "$summary") | $(extract_value hang_files "$summary") | $(extract_value queue_dir "$summary") | $(extract_value crashes_dir "$summary") | $(extract_value hangs_dir "$summary") | $(extract_value fuzzer_stats_path "$summary") | $(extract_value plot_data_path "$summary") |"
  done
} > "$OUT_MD"

cat "$OUT_MD"
