#!/usr/bin/env bash
set -euo pipefail

export DBMS="${DBMS:-mysql}"
export RUN_ID="${RUN_ID:-manual}"
export DURATION="${DURATION:-24h}"
export SEED_DIR="${SEED_DIR:-/workspace/seeds}"
export TARGET_DIR="${TARGET_DIR:-/workspace/targets}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/output}"
export LOG_DIR="${LOG_DIR:-/workspace/logs}"
export RUNTIME_DIR="${RUNTIME_DIR:-/workspace/runtime}"
export AFL_TIMEOUT="${AFL_TIMEOUT:-1000}"
export MEMORY_LIMIT="${MEMORY_LIMIT:-4096}"
export SCHEDULER_CONFIG="${SCHEDULER_CONFIG:-}"

cmd="${1:-run}"
case "$cmd" in
  smoke)
    export DURATION="${SMOKE_DURATION:-$DURATION}"
    export POLL_INTERVAL="${POLL_INTERVAL:-5}"
    exec /opt/sqleek/stage3_scheduler/docker/common/run_scheduler.sh smoke
    ;;
  run)
    exec /opt/sqleek/stage3_scheduler/docker/common/run_scheduler.sh run
    ;;
  shell)
    shift || true
    exec /bin/bash "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
