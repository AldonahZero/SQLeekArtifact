#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/SQLeek/experiment/RQ2/dynsql
OUT="$ROOT/output/postgresql-afl-local/afl-5min"
RUNTIME="$ROOT/runtime/postgresql-afl/afl-5min"
LOG="$ROOT/logs/postgresql-afl-build/afl-5min"
mkdir -p "$OUT" "$RUNTIME" "$LOG"
chown -R postgres:postgres "$OUT" "$RUNTIME" "$LOG"
exec runuser -u postgres --preserve-environment -- env \
  AFL_IGNORE_PROBLEMS=1 \
  AFL_NO_FORKSRV=1 \
  AFL_SKIP_BIN_CHECK=1 \
  AFL_SKIP_CPUFREQ=1 \
  AFL_NO_UI=1 \
  "$ROOT/third_party/aflplusplus/afl-fuzz" \
    -i "$ROOT/seeds/initial" \
    -o "$OUT" \
    -V 300 \
    -t 120000 \
    -m none \
    -- python3 "$ROOT/scripts/run/postgresql_afl_single_input.py" \
      --input @@ \
      --runtime-root "$RUNTIME" \
      --log-root "$LOG" \
      --max-statements 20 \
      --quiet
