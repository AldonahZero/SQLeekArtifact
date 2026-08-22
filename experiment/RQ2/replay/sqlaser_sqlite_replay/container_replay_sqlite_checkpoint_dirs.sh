#!/usr/bin/env bash
set -euo pipefail

BINARY=
CHECKPOINTS_MIN="60,180,300,480,600,720,900,1200,1440"
SEED_TIMEOUT=10
OUT_ROOT=/rq2_out
REPEAT_ID=
RUN_ID=
TOOL=SQLaser
DBMS=sqlite
VERSION="fuzzing_sqlite=3.54.0,replay_sqlite=3.53.2"
ROLLING_MERGE_INTERVAL=${ROLLING_MERGE_INTERVAL:-50}
MIN_FREE_KB=${MIN_FREE_KB:-15728640}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary) BINARY="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --repeat-id) REPEAT_ID="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --tool) TOOL="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$BINARY" ]] || { echo "binary is missing or not executable: $BINARY" >&2; exit 2; }
[[ -n "$REPEAT_ID" ]] || { echo "--repeat-id is required" >&2; exit 2; }
[[ -n "$RUN_ID" ]] || RUN_ID="${TOOL}_${DBMS}_${REPEAT_ID}"

mkdir -p "$OUT_ROOT"
PROFILE_ROOT=/tmp/rq2_prof
TMP_ROOT=/tmp/rq2_tmp
DB_ROOT=/rq2_sqlite_tmp
mkdir -p "$PROFILE_ROOT" "$TMP_ROOT" "$DB_ROOT"
chmod 0777 "$PROFILE_ROOT" "$TMP_ROOT" "$DB_ROOT"
rm -rf -- "$PROFILE_ROOT"/* "$TMP_ROOT"/* "$DB_ROOT"/*

IFS=',' read -r -a CHECKPOINTS <<< "$CHECKPOINTS_MIN"

GLOBAL_SEEDS="$OUT_ROOT/replay_index_seeds.tsv"
GLOBAL_RESULTS="$OUT_ROOT/seed_results.tsv"
LEAK_LOG="$OUT_ROOT/leak_monitor.tsv"
REPLAY_INDEX="$OUT_ROOT/replay_index.tsv"
STOP_FILE="$OUT_ROOT/STOPPED.json"

printf 'seed_index\tcheckpoint_min\telapsed_ms\tseed_name\tseed_path\texit_code\n' > "$GLOBAL_RESULTS"
printf 'seed_index\tcheckpoint_min\telapsed_ms\tseed_name\tseed_path\n' > "$GLOBAL_SEEDS"
printf 'timestamp_utc\trepeat_id\tcheckpoint_min\tseed_index\tdb_files\tdb_kb\tprofile_files\tprofile_kb\tout_free_kb\n' > "$LEAK_LOG"
printf 'run_id\ttool\tdbms\tversion\trepeat_id\tcheckpoint_min\tcov_json\tprofdata\treport\tseed_corpus\tseed_count\tstart_time\tend_time\tbuild_id\tcontainer_id\tstatus\tmessage\n' > "$REPLAY_INDEX"

success_count=0
failure_count=0
timeout_count=0
seed_index=0
profraw_merged=0
forced_leak_cleanups=0
start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cleanup_dir() {
  local dir="$1"
  rm -f -- "$dir"/*.db "$dir"/*.db-journal "$dir"/*.db-wal "$dir"/*.db-shm 2>/dev/null || true
  find "$dir" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
}

cleanup_all() {
  find "$DB_ROOT" -mindepth 1 -maxdepth 2 -type f -delete 2>/dev/null || true
  find "$TMP_ROOT" -mindepth 1 -maxdepth 2 -type f -delete 2>/dev/null || true
}

trap cleanup_all EXIT
trap 'exit 130' INT TERM

guard() {
  local checkpoint_min="$1"
  local db_dir="$2"
  local prof_dir="$3"
  local db_files db_kb profile_files profile_kb free_kb
  db_files=$(find "$db_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  db_kb=$(du -sk "$db_dir" | awk '{print $1}')
  profile_files=$(find "$prof_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  profile_kb=$(du -sk "$prof_dir" | awk '{print $1}')
  free_kb=$(df -Pk "$OUT_ROOT" | awk 'NR == 2 {print $4}')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$REPEAT_ID" "$checkpoint_min" "$seed_index" \
    "$db_files" "$db_kb" "$profile_files" "$profile_kb" "$free_kb" >> "$LEAK_LOG"
  if [[ "$free_kb" -lt "$MIN_FREE_KB" ]]; then
    cat > "$STOP_FILE" <<JSON
{"status":"stopped","reason":"disk_guard","free_kb":$free_kb,"min_free_kb":$MIN_FREE_KB,"repeat_id":"$REPEAT_ID","checkpoint_min":$checkpoint_min,"seed_index":$seed_index}
JSON
    exit 9
  fi
  if [[ "$db_files" -ne 0 || "$db_kb" -gt 16 ]]; then
    cat > "$STOP_FILE" <<JSON
{"status":"stopped","reason":"sqlite_tmp_leak","db_files":$db_files,"db_kb":$db_kb,"repeat_id":"$REPEAT_ID","checkpoint_min":$checkpoint_min,"seed_index":$seed_index}
JSON
    exit 10
  fi
}

rolling_merge() {
  local prof_dir="$1"
  local cumulative="$PROFILE_ROOT/cumulative.profdata"
  shopt -s nullglob
  local raw=("$prof_dir"/*.profraw)
  if [[ ${#raw[@]} -eq 0 ]]; then
    shopt -u nullglob
    return 0
  fi
  local next="$PROFILE_ROOT/cumulative.next.profdata"
  profraw_merged=$((profraw_merged + ${#raw[@]}))
  if [[ -s "$cumulative" ]]; then
    "$LLVM_PROFDATA_BIN" merge -sparse "$cumulative" "${raw[@]}" -o "$next"
  else
    "$LLVM_PROFDATA_BIN" merge -sparse "${raw[@]}" -o "$next"
  fi
  mv -f "$next" "$cumulative"
  rm -f -- "${raw[@]}"
  shopt -u nullglob
}

write_bucket_from_checkpoint() {
  local checkpoint_min="$1"
  local seen_file="$2"
  local bucket_file="$3"
  local checkpoint_dir="/rq2_checkpoints/checkpoint_$(printf '%04d' "$checkpoint_min")m/queue"
  [[ -d "$checkpoint_dir" ]] || { echo "missing checkpoint queue: $checkpoint_dir" >&2; exit 3; }
  python3 - "$checkpoint_dir" "$seen_file" "$bucket_file" <<'PY'
from __future__ import annotations
import re, sys
from pathlib import Path

checkpoint_dir = Path(sys.argv[1])
seen_file = Path(sys.argv[2])
bucket_file = Path(sys.argv[3])
seen = set(seen_file.read_text().splitlines()) if seen_file.exists() else set()
rows = []
bad = []
for path in checkpoint_dir.iterdir():
    if not path.is_file():
        continue
    name = path.name
    match = re.search(r'(?:^|,)time:(\d+)(?:,|$)', name)
    if not match:
        bad.append(name)
        continue
    if name in seen:
        continue
    rows.append((int(match.group(1)), name, str(path)))
if bad:
    raise SystemExit(f'unparseable queue timestamps: count={len(bad)} examples={bad[:10]}')
rows.sort(key=lambda row: (row[0], row[1]))
with bucket_file.open('w', encoding='utf-8') as fp:
    for elapsed, name, path in rows:
        fp.write(f'{elapsed}\t{name}\t{path}\n')
with seen_file.open('a', encoding='utf-8') as fp:
    for _, name, _ in rows:
        fp.write(name + '\n')
PY
}

SEEN_FILE=/tmp/rq2_seen_seed_names.txt
: > "$SEEN_FILE"

for checkpoint_min in "${CHECKPOINTS[@]}"; do
  cp_dir="$OUT_ROOT/checkpoint_$(printf '%04d' "$checkpoint_min")m"
  profile_dir="$PROFILE_ROOT/checkpoint_$(printf '%04d' "$checkpoint_min")m"
  db_dir="$DB_ROOT/checkpoint_$(printf '%04d' "$checkpoint_min")m"
  tmp_dir="$TMP_ROOT/checkpoint_$(printf '%04d' "$checkpoint_min")m"
  mkdir -p "$cp_dir"/{coverage,logs,profile,manifests} "$profile_dir" "$db_dir" "$tmp_dir"
  chmod 0777 "$profile_dir" "$db_dir" "$tmp_dir"
  rm -rf -- "$profile_dir"/* "$db_dir"/* "$tmp_dir"/*

  bucket_file="$cp_dir/bucket_seeds.tsv"
  write_bucket_from_checkpoint "$checkpoint_min" "$SEEN_FILE" "$bucket_file"
  bucket_count=$(wc -l < "$bucket_file" | tr -d ' ')
  checkpoint_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  since_merge=0
  export TMPDIR="$tmp_dir"
  export SQLITE_TMPDIR="$db_dir"
  export LLVM_PROFILE_FILE="%c${profile_dir}/%p-%m.profraw"

  while IFS=$'\t' read -r elapsed name seed; do
    [[ -n "${seed:-}" ]] || continue
    seed_index=$((seed_index + 1))
    since_merge=$((since_merge + 1))
    cleanup_dir "$db_dir"
    cleanup_dir "$tmp_dir"
    db="$db_dir/seed_${seed_index}.db"
    set +e
    timeout --kill-after=5 "$SEED_TIMEOUT" "$BINARY" "$db" < "$seed" >> "$cp_dir/logs/replay.stdout" 2>> "$cp_dir/logs/replay.stderr"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      success_count=$((success_count + 1))
    elif [[ "$rc" -eq 124 ]]; then
      timeout_count=$((timeout_count + 1))
    else
      failure_count=$((failure_count + 1))
    fi
    cleanup_dir "$db_dir"
    cleanup_dir "$tmp_dir"
    remaining=$(find "$db_dir" "$tmp_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
    if [[ "$remaining" -ne 0 ]]; then
      forced_leak_cleanups=$((forced_leak_cleanups + 1))
      cleanup_dir "$db_dir"
      cleanup_dir "$tmp_dir"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$seed_index" "$checkpoint_min" "$elapsed" "$name" "$seed" "$rc" >> "$GLOBAL_RESULTS"
    printf '%s\t%s\t%s\t%s\t%s\n' "$seed_index" "$checkpoint_min" "$elapsed" "$name" "$seed" >> "$GLOBAL_SEEDS"
    if [[ "$since_merge" -ge "$ROLLING_MERGE_INTERVAL" ]]; then
      rolling_merge "$profile_dir"
      since_merge=0
    fi
    if (( seed_index % 25 == 0 )); then
      guard "$checkpoint_min" "$db_dir" "$profile_dir"
    fi
  done < "$bucket_file"

  rolling_merge "$profile_dir"
  guard "$checkpoint_min" "$db_dir" "$profile_dir"
  cumulative_prof="$PROFILE_ROOT/cumulative.profdata"
  [[ -s "$cumulative_prof" ]] || { echo "missing cumulative profdata at checkpoint $checkpoint_min" >&2; exit 5; }
  cp -p "$cumulative_prof" "$cp_dir/profile/${RUN_ID}_t${checkpoint_min}.profdata"
  cp -p "$GLOBAL_SEEDS" "$cp_dir/manifests/executed_seeds_cumulative.tsv"
  "$LLVM_COV_BIN" export -format=text "$BINARY" -instr-profile="$cp_dir/profile/${RUN_ID}_t${checkpoint_min}.profdata" > "$cp_dir/coverage/${RUN_ID}_t${checkpoint_min}.cov.json"
  "$LLVM_COV_BIN" report "$BINARY" -instr-profile="$cp_dir/profile/${RUN_ID}_t${checkpoint_min}.profdata" > "$cp_dir/coverage/${RUN_ID}_t${checkpoint_min}.report.txt"
  checkpoint_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  cov_json="$cp_dir/coverage/${RUN_ID}_t${checkpoint_min}.cov.json"
  profdata="$cp_dir/profile/${RUN_ID}_t${checkpoint_min}.profdata"
  report="$cp_dir/coverage/${RUN_ID}_t${checkpoint_min}.report.txt"
  total_seen=$(wc -l < "$GLOBAL_SEEDS" | tr -d ' ')
  cat > "$cp_dir/manifests/checkpoint_manifest.json" <<JSON
{
  "run_id": "$RUN_ID",
  "repeat_id": "$REPEAT_ID",
  "checkpoint_min": $checkpoint_min,
  "bucket_seed_count": $bucket_count,
  "cumulative_seed_count": $total_seen,
  "seed_timeout_seconds": $SEED_TIMEOUT,
  "rolling_merge_interval": $ROLLING_MERGE_INTERVAL,
  "tmp_profile_dir": "$profile_dir",
  "tmp_db_dir": "$db_dir",
  "tmp_dir": "$tmp_dir",
  "start_time": "$checkpoint_start",
  "end_time": "$checkpoint_end",
  "coverage_json": "$cov_json",
  "profdata": "$profdata",
  "status": "complete"
}
JSON
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\tcomplete\t\n' \
    "$RUN_ID" "$TOOL" "$DBMS" "$VERSION" "$REPEAT_ID" "$checkpoint_min" \
    "$cov_json" "$profdata" "$report" "/rq2_checkpoints/checkpoint_$(printf '%04d' "$checkpoint_min")m/queue" \
    "$total_seen" "$start_time" "$checkpoint_end" "$BINARY" "${HOSTNAME:-container}" >> "$REPLAY_INDEX"
done

cleanup_all
final_db_files=$(find "$DB_ROOT" "$TMP_ROOT" -mindepth 1 -type f | wc -l | tr -d ' ')
end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$OUT_ROOT/run_summary.json" <<JSON
{
  "run_id": "$RUN_ID",
  "repeat_id": "$REPEAT_ID",
  "status": "complete",
  "start_time": "$start_time",
  "end_time": "$end_time",
  "seeds_executed_once": $seed_index,
  "successful_seeds": $success_count,
  "failed_seeds": $failure_count,
  "timed_out_seeds": $timeout_count,
  "profraw_files_merged": $profraw_merged,
  "forced_leak_cleanups": $forced_leak_cleanups,
  "final_tmp_files": $final_db_files,
  "min_free_kb": $MIN_FREE_KB
}
JSON

if [[ "$final_db_files" -ne 0 ]]; then
  exit 10
fi
