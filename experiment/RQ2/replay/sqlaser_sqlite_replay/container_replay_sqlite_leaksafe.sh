#!/usr/bin/env bash
set -euo pipefail

BINARY=
CHECKPOINTS_MS=
SEED_TIMEOUT=30
OUT_PREFIX=/rq2_out/replay
ROLLING_MERGE_INTERVAL=${ROLLING_MERGE_INTERVAL:-100}
MIN_FREE_KB=${MIN_FREE_KB:-15728640}
PROFILE_DIR=/tmp/rq2_prof
DB_TMP=/rq2_sqlite_tmp
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary) BINARY="$2"; shift 2 ;;
    --checkpoints-ms) CHECKPOINTS_MS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --out-prefix) OUT_PREFIX="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$BINARY" && -n "$CHECKPOINTS_MS" ]] || { echo "binary/checkpoints missing" >&2; exit 2; }
mkdir -p "$PROFILE_DIR" "$DB_TMP" "$(dirname "$OUT_PREFIX")"
chmod 0777 "$PROFILE_DIR" "$DB_TMP"
rm -rf -- "$PROFILE_DIR"/* "$DB_TMP"/*
export LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw'
export SQLITE_TMPDIR="$DB_TMP"
export TMPDIR="$DB_TMP"

LEAK_LOG=${OUT_PREFIX}.leak_monitor.tsv
printf 'timestamp_utc\tseed_index\tcheckpoint_min\tdb_files\tdb_kb\tprofile_files\tprofile_kb\toutput_free_kb\n' > "$LEAK_LOG"
leak_cleanups=0
profraw_merged=0
success_count=0
failure_count=0
timeout_count=0
: > "${OUT_PREFIX}.seed_results.tsv"
printf 'seed_index\tcheckpoint_min\telapsed_ms\tseed_name\texit_code\n' > "${OUT_PREFIX}.seed_results.tsv"

cleanup_db() {
  rm -f -- "$DB_TMP"/*.db "$DB_TMP"/*.db-journal "$DB_TMP"/*.db-wal "$DB_TMP"/*.db-shm 2>/dev/null || true
}

cleanup_all() {
  cleanup_db
  find "$DB_TMP" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
}

trap cleanup_all EXIT
trap 'exit 130' INT TERM

record_guard() {
  local seed_index=$1 checkpoint_min=$2
  local db_files db_kb profile_files profile_kb free_kb
  db_files=$(find "$DB_TMP" -maxdepth 1 -type f | wc -l | tr -d ' ')
  db_kb=$(du -sk "$DB_TMP" | awk '{print $1}')
  profile_files=$(find "$PROFILE_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')
  profile_kb=$(du -sk "$PROFILE_DIR" | awk '{print $1}')
  free_kb=$(df -Pk /rq2_out | awk 'NR == 2 {print $4}')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$seed_index" "$checkpoint_min" "$db_files" "$db_kb" "$profile_files" "$profile_kb" "$free_kb" >> "$LEAK_LOG"
  if [[ "$free_kb" -lt "$MIN_FREE_KB" ]]; then
    echo "disk guard triggered: free_kb=$free_kb minimum=$MIN_FREE_KB" >&2
    exit 9
  fi
  if [[ "$db_files" -ne 0 || "$db_kb" -gt 16 ]]; then
    echo "sqlite db leak guard triggered: files=$db_files kb=$db_kb" >&2
    exit 10
  fi
  if [[ "$profile_kb" -gt 1572864 ]]; then
    echo "profile tmpfs guard triggered: profile_kb=$profile_kb" >&2
    exit 11
  fi
}

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MS"
python3 - "$CHECKPOINTS_MS" <<'PY'
from __future__ import annotations
import re, sys
from pathlib import Path

checkpoints = sorted({int(x) for x in sys.argv[1].split(',') if x})
rows = []
bad = []
for path in Path('/rq2_queue').iterdir():
    if not path.is_file():
        continue
    match = re.search(r'(?:^|,)time:(\d+)(?:,|$)', path.name)
    if not match:
        bad.append(path.name)
        continue
    rows.append((int(match.group(1)), path.name, str(path)))
if bad:
    raise SystemExit(f'unparseable queue timestamps: count={len(bad)} examples={bad[:10]}')
rows.sort(key=lambda row: (row[0], row[1]))
previous = -1
for checkpoint in checkpoints:
    minute = checkpoint // 60000
    bucket = [row for row in rows if previous < row[0] <= checkpoint]
    cumulative = [row for row in rows if row[0] <= checkpoint]
    with open(f'/tmp/rq2_bucket_{minute}.tsv', 'w', encoding='utf-8') as fp:
        for elapsed, name, path in bucket:
            fp.write(f'{elapsed}\t{name}\t{path}\n')
    with open(f'/tmp/rq2_cumulative_{minute}.tsv', 'w', encoding='utf-8') as fp:
        for elapsed, name, path in cumulative:
            fp.write(f'{elapsed}\t{name}\t{path}\n')
    previous = checkpoint
PY

rolling_merge() {
  shopt -s nullglob
  local raw=("$PROFILE_DIR"/*.profraw)
  local cumulative="$PROFILE_DIR/cumulative.profdata"
  if [[ ${#raw[@]} -eq 0 ]]; then
    shopt -u nullglob
    return 0
  fi
  local next="$PROFILE_DIR/cumulative.next.profdata"
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

snapshot_coverage() {
  local checkpoint_min=$1
  local prefix=${OUT_PREFIX}_t${checkpoint_min}
  local cumulative_tsv=/tmp/rq2_cumulative_${checkpoint_min}.tsv
  rolling_merge
  [[ -s "$PROFILE_DIR/cumulative.profdata" ]] || { echo "missing cumulative profile" >&2; exit 5; }
  cp -p "$cumulative_tsv" "${prefix}.executed_seeds.tsv"
  cp -p "$PROFILE_DIR/cumulative.profdata" "${prefix}.profdata"
  "$LLVM_COV_BIN" export -format=text "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.cov.json"
  "$LLVM_COV_BIN" report "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt"
  local seed_count
  seed_count=$(wc -l < "$cumulative_tsv" | tr -d ' ')
  printf 'seed_count\t%s\nprofile_count\t1\nrolling_merge_interval\t%s\n' \
    "$seed_count" "$ROLLING_MERGE_INTERVAL" > "${prefix}.meta.tsv"
}

n=0
since_merge=0
last_checkpoint_min=0
for checkpoint_ms in "${CHECKPOINT_ARR[@]}"; do
  checkpoint_min=$((checkpoint_ms / 60000))
  last_checkpoint_min=$checkpoint_min
  bucket=/tmp/rq2_bucket_${checkpoint_min}.tsv
  while IFS=$'\t' read -r _elapsed _name seed; do
    [[ -n "${seed:-}" ]] || continue
    n=$((n + 1))
    since_merge=$((since_merge + 1))
    cleanup_db
    db=$DB_TMP/seed_${n}.db
    set +e
    timeout --kill-after=5 "$SEED_TIMEOUT" "$BINARY" "$db" < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      success_count=$((success_count + 1))
    elif [[ "$rc" -eq 124 ]]; then
      timeout_count=$((timeout_count + 1))
    else
      failure_count=$((failure_count + 1))
    fi
    cleanup_db
    remaining=$(find "$DB_TMP" -maxdepth 1 -type f | wc -l | tr -d ' ')
    if [[ "$remaining" -ne 0 ]]; then
      leak_cleanups=$((leak_cleanups + 1))
      cleanup_all
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$checkpoint_min" "$_elapsed" "$_name" "$rc" >> "${OUT_PREFIX}.seed_results.tsv"
    if [[ "$since_merge" -ge "$ROLLING_MERGE_INTERVAL" ]]; then
      rolling_merge
      since_merge=0
    fi
    if (( n % 25 == 0 )); then
      record_guard "$n" "$checkpoint_min"
    fi
  done < "$bucket"
  snapshot_coverage "$checkpoint_min"
  since_merge=0
  record_guard "$n" "$checkpoint_min"
done

cleanup_all
record_guard "$n" "$last_checkpoint_min"
if [[ "$profraw_merged" -le 0 ]]; then
  echo "no profraw files were generated" >&2
  exit 12
fi
cat > "${OUT_PREFIX}.db_leak_summary.json" <<JSON
{
  "db_tmp": "$DB_TMP",
  "db_tmp_is_tmpfs": true,
  "final_db_files": 0,
  "forced_leak_cleanups": $leak_cleanups,
  "profraw_files_merged": $profraw_merged,
  "seeds_executed": $n,
  "successful_seeds": $success_count,
  "failed_seeds": $failure_count,
  "timed_out_seeds": $timeout_count,
  "status": "complete"
}
JSON
