#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/sqlite}
STAGE1_TARGET_DIR=${STAGE1_TARGET_DIR:-/root/SQLeek/sqleek_pipeline/stage1_static/output/targets}
OUT_DIR=$WORK_DIR/targets
OUT=$OUT_DIR/sqlite_rq2_targets.txt
MANIFEST=$OUT_DIR/sqlite_rq2_targets_manifest.txt

mkdir -p "$OUT_DIR"

SOURCES=(
  "$STAGE1_TARGET_DIR/sqlite_memory.txt"
  "$STAGE1_TARGET_DIR/sqlite_stale.txt"
  "$STAGE1_TARGET_DIR/sqlite_logic.txt"
)

for src in "${SOURCES[@]}"; do
  if [ ! -f "$src" ]; then
    echo "missing Stage 1 target file: $src" >&2
    exit 1
  fi
done

awk '
  /^[[:space:]]*$/ { next }
  {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
    if ($0 ~ /^[A-Za-z0-9_./-]+\.c:[0-9]+$/) print $0
  }
' "${SOURCES[@]}" | sort -u > "$OUT"

if [ ! -s "$OUT" ]; then
  echo "no valid SQLite AFLGo targets generated" >&2
  exit 1
fi

{
  echo "target_list=$OUT"
  echo "stage1_sources=${SOURCES[*]}"
  for src in "${SOURCES[@]}"; do
    echo "$(basename "$src")_raw_lines=$(wc -l < "$src")"
    echo "$(basename "$src")_valid_lines=$(awk '/^[[:space:]]*$/ { next } /^[A-Za-z0-9_./-]+\.c:[0-9]+$/ { c++ } END { print c+0 }' "$src")"
  done
  echo "deduped_targets=$(wc -l < "$OUT")"
  echo "sha256=$(sha256sum "$OUT" | awk '{print $1}')"
} | tee "$MANIFEST"
