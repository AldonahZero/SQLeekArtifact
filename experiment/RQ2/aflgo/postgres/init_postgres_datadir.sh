#!/usr/bin/env bash
set -e
WORK_DIR=${WORK_DIR:-/root/SQLeek/experiment/RQ2/aflgo/postgres}
BIN_DIR=$WORK_DIR/bin
DATADIR=${1:-$WORK_DIR/runtime/datadir}
rm -rf "$DATADIR"
mkdir -p "$DATADIR"
chown -R postgres:postgres "$WORK_DIR/runtime" "$DATADIR"
runuser -u postgres -- "$BIN_DIR/initdb_aflgo" -D "$DATADIR" > "$WORK_DIR/logs/initdb_$(basename "$DATADIR").log" 2>&1
cat >> "$DATADIR/postgresql.conf" <<'CONF'
fsync = off
full_page_writes = off
synchronous_commit = off
shared_buffers = '16MB'
max_wal_size = '64MB'
log_min_messages = fatal
statement_timeout = '1000ms'
CONF
chown postgres:postgres "$DATADIR/postgresql.conf"
echo "$DATADIR"
