#!/usr/bin/env bash
set -euo pipefail

DBMS="${DBMS:-mysql}"
[ "$DBMS" = "postgresql" ] && DBMS="postgres"
OUTPUT_DIR="${OUTPUT_DIR:-/workspace/output}"
LOG_DIR="${LOG_DIR:-/workspace/logs}"

[ -x /opt/aflplusplus/afl-fuzz ]
[ -d /opt/sqlright ]
[ -s "$LOG_DIR/scheduler.log" ] || exit 1
[ -d "$OUTPUT_DIR/${DBMS}_memory/default/queue" ] || exit 1
