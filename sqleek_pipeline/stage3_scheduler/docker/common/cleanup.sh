#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${RUNTIME_DIR:-/workspace/runtime}"

pkill -TERM -x mysqld 2>/dev/null || true
pkill -TERM -x postgres 2>/dev/null || true
pkill -TERM -x mserver5 2>/dev/null || true
sleep 1
pkill -KILL -x mysqld 2>/dev/null || true
pkill -KILL -x postgres 2>/dev/null || true
pkill -KILL -x mserver5 2>/dev/null || true

find "$RUNTIME_DIR" -maxdepth 3 \( -name '*.pid' -o -name '*.sock' -o -name '*.lock' \) -type f -delete 2>/dev/null || true
