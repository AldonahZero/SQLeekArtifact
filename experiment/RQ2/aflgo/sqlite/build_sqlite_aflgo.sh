#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
SQLITE_SRC=${SQLITE_SRC:-/root/SQLeek/sources/sqlite}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/sqlite}
BUILD_ROOT=$WORK_DIR/build
TMP_DIR=$WORK_DIR/tmp/distance
LOG_DIR=$WORK_DIR/logs
BIN_DIR=$WORK_DIR/bin
SOURCE_TARGETS_FILE=${SOURCE_TARGETS_FILE:-$WORK_DIR/targets/sqlite_rq2_targets.txt}
TARGETS_FILE=$TMP_DIR/BBtargets.txt

mkdir -p "$BUILD_ROOT" "$TMP_DIR" "$LOG_DIR" "$BIN_DIR"

if [ ! -x "$AFLGO_DIR/instrument/aflgo-clang" ]; then
  echo "missing AFLGo compiler: $AFLGO_DIR/instrument/aflgo-clang" >&2
  exit 1
fi

if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ] &&
   [ -x "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" ]; then
  ln -sf "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" \
    "$AFLGO_DIR/distance/distance_calculator/distance.bin"
fi

if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ]; then
  echo "missing AFLGo distance calculator: $AFLGO_DIR/distance/distance_calculator/distance.bin" >&2
  exit 1
fi

if [ ! -x "$SQLITE_SRC/configure" ]; then
  echo "missing SQLite configure script: $SQLITE_SRC/configure" >&2
  exit 1
fi

if [ ! -s "$SOURCE_TARGETS_FILE" ]; then
  echo "missing AFLGo target list: $SOURCE_TARGETS_FILE" >&2
  echo "run $WORK_DIR/prepare_rq2_targets.sh first, or set SOURCE_TARGETS_FILE explicitly" >&2
  exit 1
fi
cp "$SOURCE_TARGETS_FILE" "$TARGETS_FILE"

mkdir -p /usr/lib/bfd-plugins
if [ -f /usr/lib/llvm-11/lib/LLVMgold.so ]; then
  ln -sf /usr/lib/llvm-11/lib/LLVMgold.so /usr/lib/bfd-plugins/LLVMgold.so
fi
if [ -f /usr/lib/llvm-11/lib/libLTO.so ]; then
  ln -sf /usr/lib/llvm-11/lib/libLTO.so /usr/lib/bfd-plugins/libLTO.so
fi

export AFLGO="$AFLGO_DIR"
export AFL_CC=${AFL_CC:-clang-11}
export AFL_CXX=${AFL_CXX:-clang++-11}
export CC="$AFLGO_DIR/instrument/aflgo-clang"
export CXX="$AFLGO_DIR/instrument/aflgo-clang++"
export PATH="/usr/lib/llvm-11/bin:$PATH"
export AFL_QUIET=1
export AFL_DONT_OPTIMIZE=1

rm -rf "$BUILD_ROOT/sqlite-preprocess" "$BUILD_ROOT/sqlite-instrumented"
rm -rf "$TMP_DIR/dot-files"
rm -f "$TMP_DIR"/BBnames.txt "$TMP_DIR"/BBcalls.txt "$TMP_DIR"/Fnames.txt \
      "$TMP_DIR"/Ftargets.txt "$TMP_DIR"/distance.cfg.txt "$TMP_DIR"/step*.log \
      "$TMP_DIR"/state-fast "$TMP_DIR"/callgraph.distance.txt

COMMON_CONFIGURE=(
  --disable-shared
  --disable-readline
  --disable-load-extension
  --linemacros
)

echo "[1/4] AFLGo preprocessing build"
mkdir -p "$BUILD_ROOT/sqlite-preprocess"
(
  cd "$BUILD_ROOT/sqlite-preprocess"
  export CFLAGS="-g -O0 -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto -fuse-ld=gold -Wl,-plugin-opt=save-temps"
  export CXXFLAGS="$CFLAGS"
  export LDFLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps"
  "$SQLITE_SRC/configure" "${COMMON_CONFIGURE[@]}"
  make clean >/dev/null 2>&1 || true
  make -j1 sqlite3
)

echo "[2/4] Cleaning AFLGo distance inputs"
if [ ! -s "$TMP_DIR/BBnames.txt" ]; then
  echo "AFLGo did not produce BBnames.txt" >&2
  exit 1
fi

grep -v '^$' "$TMP_DIR/BBnames.txt" | rev | cut -d: -f2- | rev | sort -u > "$TMP_DIR/BBnames.clean"
mv "$TMP_DIR/BBnames.clean" "$TMP_DIR/BBnames.txt"

if [ -s "$TMP_DIR/BBcalls.txt" ]; then
  grep -Ev '^[^,]*$|^([^,]*,){2,}[^,]*$' "$TMP_DIR/BBcalls.txt" | sort -u > "$TMP_DIR/BBcalls.clean" || true
  mv "$TMP_DIR/BBcalls.clean" "$TMP_DIR/BBcalls.txt"
else
  : > "$TMP_DIR/BBcalls.txt"
fi

echo "[3/4] Computing AFLGo distances"
python3 "$AFLGO_DIR/distance/gen_distance_fast.py" "$BUILD_ROOT/sqlite-preprocess" "$TMP_DIR" sqlite3
if [ ! -s "$TMP_DIR/distance.cfg.txt" ]; then
  echo "distance.cfg.txt is empty; see $TMP_DIR/step*.log" >&2
  exit 1
fi

echo "[4/4] Final distance-instrumented SQLite build"
mkdir -p "$BUILD_ROOT/sqlite-instrumented"
(
  cd "$BUILD_ROOT/sqlite-instrumented"
  export CFLAGS="-g -O1 -distance=$TMP_DIR/distance.cfg.txt"
  export CXXFLAGS="$CFLAGS"
  "$SQLITE_SRC/configure" "${COMMON_CONFIGURE[@]}"
  make clean >/dev/null 2>&1 || true
  make -j1 sqlite3
)

cp "$BUILD_ROOT/sqlite-instrumented/sqlite3" "$BIN_DIR/sqlite3_aflgo"
chmod +x "$BIN_DIR/sqlite3_aflgo"

"$CC" \
  -DNDEBUG -fPIC -g -O1 -distance="$TMP_DIR/distance.cfg.txt" \
  -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_PERCENTILE \
  -DSQLITE_HAVE_ZLIB=1 -DSQLITE_OMIT_LOAD_EXTENSION=1 -DSQLITE_THREADSAFE=1 \
  -D_HAVE_SQLITE_CONFIG_H -I"$BUILD_ROOT/sqlite-instrumented" \
  -I"$SQLITE_SRC/src" -I"$SQLITE_SRC/ext/rtree" -I"$SQLITE_SRC/ext/icu" \
  -I"$SQLITE_SRC/ext/fts3" -I"$SQLITE_SRC/ext/session" -I"$SQLITE_SRC/ext/misc" \
  "$WORK_DIR/sqlite_harness.c" "$BUILD_ROOT/sqlite-instrumented/sqlite3.c" \
  -o "$BIN_DIR/sqlite_harness_aflgo" -lm -lz
chmod +x "$BIN_DIR/sqlite_harness_aflgo"

{
  echo "sqlite_binary=$BIN_DIR/sqlite3_aflgo"
  echo "harness_binary=$BIN_DIR/sqlite_harness_aflgo"
  echo "source_targets_file=$SOURCE_TARGETS_FILE"
  echo "targets_file=$TARGETS_FILE"
  echo "targets_count=$(wc -l < "$TARGETS_FILE")"
  echo "targets_sha256=$(sha256sum "$TARGETS_FILE" | awk '{print $1}')"
  echo "distance_file=$TMP_DIR/distance.cfg.txt"
  echo "bb_count=$(wc -l < "$TMP_DIR/BBnames.txt")"
  echo "ftarget_count=$(wc -l < "$TMP_DIR/Ftargets.txt")"
  echo "distance_count=$(wc -l < "$TMP_DIR/distance.cfg.txt")"
  echo "sqlite_source=$(cd "$SQLITE_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "build_command=$0"
} | tee "$LOG_DIR/sqlite_build_summary.txt"
