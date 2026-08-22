#!/usr/bin/env bash
# Run inside `codeql database create --command` so traced compiles repopulate the DB.
# Without `make clean`, incremental `make` often does nothing → tiny DB → empty query CSVs.
# Set SQLEEK_CODEQL_SKIP_MAKE_CLEAN=1 to skip clean (faster, only if you know extraction is complete).
set -euo pipefail

PG_SRC="${1:-/tmp/pg_src}"
cd "$PG_SRC"

if [[ "${SQLEEK_CODEQL_SKIP_MAKE_CLEAN:-}" != 1 ]]; then
  if [[ -f GNUmakefile ]] || [[ -f Makefile ]]; then
    make clean
  fi
fi

./configure --without-readline --without-zlib --without-openssl
make -j"$(nproc)"
