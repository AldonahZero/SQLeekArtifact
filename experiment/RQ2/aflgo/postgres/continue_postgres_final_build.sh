#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
POSTGRES_SRC=${POSTGRES_SRC:-/root/SQLeek/sources/postgres}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/postgres}
AFLGO_INSTRUMENT_DIR=${AFLGO_INSTRUMENT_DIR:-$AFLGO_DIR/instrument_llvm14}
LLVM_DIR=${LLVM_DIR:-/usr/lib/llvm-14}
BUILD_ROOT=$WORK_DIR/build
TMP_DIR=$WORK_DIR/tmp/distance
LOG_DIR=$WORK_DIR/logs
BIN_DIR=$WORK_DIR/bin
TARGETS_FILE=$WORK_DIR/targets/postgres_rq2_targets.txt
FINAL_PREFIX=$BUILD_ROOT/postgres-install
BUILD_JOBS=${BUILD_JOBS:-1}

if [ ! -s "$TMP_DIR/distance.cfg.txt" ]; then echo "missing distance.cfg.txt" >&2; exit 1; fi
if [ ! -x "$AFLGO_INSTRUMENT_DIR/aflgo-clang" ]; then echo "missing AFLGo compiler" >&2; exit 1; fi

mkdir -p "$LOG_DIR" "$BIN_DIR"
export AFL_CC=${AFL_CC:-$LLVM_DIR/bin/clang}
export AFL_CXX=${AFL_CXX:-$LLVM_DIR/bin/clang++}
export CC="$AFLGO_INSTRUMENT_DIR/aflgo-clang"
export CXX="$AFLGO_INSTRUMENT_DIR/aflgo-clang++"
export PATH="$LLVM_DIR/bin:$PATH"
export AFL_QUIET=1
export AFL_DONT_OPTIMIZE=1

COMMON_CONFIGURE=(
  --without-readline
  --without-zlib
  --without-icu
)

rm -rf "$BUILD_ROOT/postgres-instrumented" "$FINAL_PREFIX"
mkdir -p "$BUILD_ROOT/postgres-instrumented"
(
  cd "$BUILD_ROOT/postgres-instrumented"
  CFLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt" \
  CXXFLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt" \
  "$POSTGRES_SRC/configure" --prefix="$FINAL_PREFIX" "${COMMON_CONFIGURE[@]}"
)
make -C "$BUILD_ROOT/postgres-instrumented" -j "$BUILD_JOBS"
make -C "$BUILD_ROOT/postgres-instrumented" install

ln -sf "$FINAL_PREFIX/bin/postgres" "$BIN_DIR/postgres_aflgo"
ln -sf "$FINAL_PREFIX/bin/initdb" "$BIN_DIR/initdb_aflgo"
ln -sf "$FINAL_PREFIX/bin/pg_ctl" "$BIN_DIR/pg_ctl_aflgo"
ln -sf "$FINAL_PREFIX/bin/psql" "$BIN_DIR/psql_aflgo"

if [ ! -x "$BIN_DIR/postgres_aflgo" ] || [ ! -x "$BIN_DIR/initdb_aflgo" ]; then echo "missing installed PostgreSQL AFLGo binaries" >&2; exit 1; fi

"$CC" -DNDEBUG -g -O1 -flegacy-pass-manager -distance="$TMP_DIR/distance.cfg.txt" \
  "$WORK_DIR/postgres_single_wrapper.c" -o "$BIN_DIR/postgres_single_wrapper_aflgo"
chmod +x "$BIN_DIR/postgres_single_wrapper_aflgo"

{
  echo "postgres_binary=$BIN_DIR/postgres_aflgo"
  echo "initdb_binary=$BIN_DIR/initdb_aflgo"
  echo "pg_ctl_binary=$BIN_DIR/pg_ctl_aflgo"
  echo "psql_binary=$BIN_DIR/psql_aflgo"
  echo "wrapper_binary=$BIN_DIR/postgres_single_wrapper_aflgo"
  echo "install_prefix=$FINAL_PREFIX"
  echo "targets_file=$TARGETS_FILE"
  echo "targets_count=$(wc -l < "$TARGETS_FILE")"
  echo "targets_sha256=$(sha256sum "$TARGETS_FILE" | awk '{print $1}')"
  echo "distance_file=$TMP_DIR/distance.cfg.txt"
  echo "distance_count=$(wc -l < "$TMP_DIR/distance.cfg.txt")"
  echo "callgraph_distance_count=$(wc -l < "$TMP_DIR/callgraph.distance.txt")"
  echo "postgres_source=$(cd "$POSTGRES_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "build_jobs=$BUILD_JOBS"
  echo "llvm_dir=$LLVM_DIR"
  echo "aflgo_instrument_dir=$AFLGO_INSTRUMENT_DIR"
  echo "build_command=$0"
} | tee "$LOG_DIR/postgres_build_summary.txt"
