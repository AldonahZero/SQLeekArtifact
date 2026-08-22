#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
SQLITE_BIN=${SQLITE_BIN:-$AFLGO_DIR/sqlite/bin/sqlite3_aflgo}
SQL_FILE=${1:?usage: run_sqlite_file.sh INPUT.sql}
TIMEOUT=${SQLITE_HARNESS_TIMEOUT:-5s}

if [ ! -x "$SQLITE_BIN" ]; then
  echo "missing SQLite binary: $SQLITE_BIN" >&2
  exit 127
fi

DB=$(mktemp /tmp/aflgo_sqlite.XXXXXX.db)
trap 'rm -f "$DB" "$DB-journal" "$DB-wal" "$DB-shm"' EXIT

set +e
timeout "$TIMEOUT" "$SQLITE_BIN" "$DB" < "$SQL_FILE" >/dev/null 2>&1
rc=$?
set -e

case "$rc" in
  0|1)
    exit 0
    ;;
  124|125|126|127|134|136|137|139)
    exit "$rc"
    ;;
  *)
    if [ "$rc" -gt 128 ]; then
      exit "$rc"
    fi
    exit 0
    ;;
esac
