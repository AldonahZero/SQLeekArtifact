#!/usr/bin/env bash
set -euo pipefail

REPLAY_ROOT=${1:?replay output root is required}
SUMMARY_ROOT=${2:?parallel summary root is required}
OUTPUT_DIR=${3:?merged output directory is required}

RESUMARIZER=/root/SQLeek/experiment/RQ2/scripts/resummarize_monetdb_from_profdata.py
MERGER=/root/SQLeek/experiment/RQ4/tools/merge_monetdb_parallel_resummaries.py
TARGET_REGIONS=/root/SQLeek/experiment/RQ3/result/audit/sqleek_monetdb/target_regions.csv
INSTALL_ROOT="$REPLAY_ROOT/monetdb/install_stdlibs"
SOURCE_HOST=/root/SQLeek/sources/monetdb
IMAGE=griffin_monetdb_llvmcov:latest

mkdir -p "$SUMMARY_ROOT" "$OUTPUT_DIR"
INPUT_ROOT="$SUMMARY_ROOT/profdata_inputs"
mkdir -p "$INPUT_ROOT"

for repeat in 1 2 3 4 5; do
  input_repeat="$INPUT_ROOT/r$repeat"
  mkdir -p "$input_repeat/r$repeat"
  for profdata in "$REPLAY_ROOT/monetdb/r$repeat"/*_t*.profdata; do
    test -f "$profdata"
    ln "$profdata" "$input_repeat/r$repeat/$(basename "$profdata")"
  done
done

run_repeat() {
  local repeat=$1
  python3 "$RESUMARIZER" \
    --out-dir "$SUMMARY_ROOT/r$repeat" \
    --target-regions "$TARGET_REGIONS" \
    --profdata-root "$INPUT_ROOT/r$repeat" \
    --allow-partial-repeats \
    --image "$IMAGE" \
    --binary /monetdb_llvmcov/bin/mserver5 \
    --source-root /src \
    --install-root "$INSTALL_ROOT" \
    --source-host "$SOURCE_HOST" \
    --tool SQLeek-W/O-M1
}

pids=()
for repeat in 1 2 3 4 5; do
  run_repeat "$repeat" >"$SUMMARY_ROOT/r$repeat.log" 2>&1 &
  pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=1
done
if [[ "$failed" != 0 ]]; then
  echo "at least one parallel MonetDB resummarization failed" >&2
  exit 1
fi

python3 "$MERGER" \
  --summary-root "$SUMMARY_ROOT" \
  --output-dir "$OUTPUT_DIR" \
  --repeats 5

printf 'completed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$SUMMARY_ROOT/RESUMMARY_PARALLEL_DONE"
