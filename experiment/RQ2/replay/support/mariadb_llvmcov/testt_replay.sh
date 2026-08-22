#!/usr/bin/env bash
set -euo pipefail

source /workspace/scripts/base_env.sh
source /workspace/scripts/env.sh

tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT
cat > "$tmpfile"

SQLSIM_TIMEOUT_MS=100000 /workspace/bld_griffin/autodriver_odbc_v5_aflpp dsnForFuzzer < "$tmpfile" || true

if ! pgrep -x my_8888 >/dev/null 2>&1; then
    echo "Server crashed." >&2
    exit 42
else
    echo "Server normal." >&2
fi
