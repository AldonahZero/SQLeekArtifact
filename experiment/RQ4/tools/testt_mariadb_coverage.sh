#!/usr/bin/env bash
set -euo pipefail

# Coverage replay driver for MariaDB.  The generic Griffin testt kills the
# server with SIGKILL before every seed, which prevents LLVM profiles from
# being flushed.  The bucketed runner owns server recovery, so this variant
# leaves a healthy server alone and only terminates it after a long query.

source /workspace/scripts/base_env.sh
source /workspace/scripts/env.sh

tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT
cat > "$tmpfile"

driver_rc=0
timeout --signal=TERM --kill-after=5s 35s \
  env SQLSIM_TIMEOUT_MS=30000 \
  /workspace/bld_griffin/autodriver_odbc_v5_aflpp dsnForFuzzer < "$tmpfile" || driver_rc=$?

if [[ "$driver_rc" -eq 124 || "$driver_rc" -eq 137 || "$driver_rc" -eq 143 ]]; then
  pkill -TERM my_8888 >/dev/null 2>&1 || true
  pkill -TERM mariadbd >/dev/null 2>&1 || true
  pkill -TERM mysqld >/dev/null 2>&1 || true
  sleep 2
fi

if pgrep -x my_8888 >/dev/null 2>&1 || pgrep -x mariadbd >/dev/null 2>&1 || pgrep -x mysqld >/dev/null 2>&1; then
  echo "Server normal." >&2
else
  echo "Server unavailable after seed (driver_rc=$driver_rc)." >&2
fi

# SQL/ODBC errors are expected in the replay corpus.  The outer runner checks
# the server process and performs a clean reset if this seed took it down.
exit 0
