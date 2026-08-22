#!/usr/bin/env bash
set -e

MODE=${1:---once}
MIN_AGE_MIN=${AFLGO_SQLITE_CLEAN_MIN_AGE:-10}
INTERVAL_SEC=${AFLGO_SQLITE_CLEAN_INTERVAL_SEC:-300}
DRY_RUN=${AFLGO_SQLITE_CLEAN_DRY_RUN:-0}

if ! [[ "$MIN_AGE_MIN" =~ ^[0-9]+$ ]]; then
  echo "AFLGO_SQLITE_CLEAN_MIN_AGE must be an integer" >&2
  exit 2
fi

if ! [[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]] || [ "$INTERVAL_SEC" -lt 1 ]; then
  echo "AFLGO_SQLITE_CLEAN_INTERVAL_SEC must be a positive integer" >&2
  exit 2
fi

collect_open_tmp_paths() {
  local fd target
  for fd in /proc/[0-9]*/fd/*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    case "$target" in
      /tmp/aflgo_sqlite_*.db|/tmp/aflgo_sqlite_*.db-journal|/tmp/aflgo_sqlite_*.db-wal|/tmp/aflgo_sqlite_*.db-shm)
        printf '%s\n' "$target"
        ;;
    esac
  done
}

cleanup_once() {
  local now path mtime age_min size open_file deleted skipped bytes
  local -A open_paths=()

  while IFS= read -r open_file; do
    [ -n "$open_file" ] && open_paths["$open_file"]=1
  done < <(collect_open_tmp_paths | sort -u)

  now=$(date +%s)
  deleted=0
  skipped=0
  bytes=0
  shopt -s nullglob
  for path in /tmp/aflgo_sqlite_*.db /tmp/aflgo_sqlite_*.db-journal /tmp/aflgo_sqlite_*.db-wal /tmp/aflgo_sqlite_*.db-shm; do
    [ -e "$path" ] || continue
    if [ "${open_paths[$path]+open}" = "open" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    mtime=$(stat -c %Y "$path" 2>/dev/null || echo "$now")
    age_min=$(((now - mtime) / 60))
    if [ "$age_min" -lt "$MIN_AGE_MIN" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    size=$(stat -c %s "$path" 2>/dev/null || echo 0)
    if [ "$DRY_RUN" = "1" ]; then
      printf 'would_delete\t%s\t%s\n' "$size" "$path"
    else
      rm -f -- "$path"
    fi
    deleted=$((deleted + 1))
    bytes=$((bytes + size))
  done
  shopt -u nullglob

  printf 'timestamp_utc=%s mode=%s min_age_min=%s deleted_files=%s skipped_files=%s reclaimed_bytes=%s dry_run=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$MIN_AGE_MIN" "$deleted" "$skipped" "$bytes" "$DRY_RUN"
}

case "$MODE" in
  --once)
    cleanup_once
    ;;
  --loop)
    while true; do
      cleanup_once
      sleep "$INTERVAL_SEC"
    done
    ;;
  --dry-run)
    DRY_RUN=1
    cleanup_once
    ;;
  *)
    echo "usage: cleanup_aflgo_sqlite_tmp.sh [--once|--loop|--dry-run]" >&2
    exit 2
    ;;
esac
