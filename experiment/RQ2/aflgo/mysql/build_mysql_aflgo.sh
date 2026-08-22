#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MYSQL_SRC=${MYSQL_SRC:-/root/SQLeek/sources/mysql}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mysql}
AFLGO_INSTRUMENT_DIR=${AFLGO_INSTRUMENT_DIR:-$AFLGO_DIR/instrument_llvm14}
LLVM_DIR=${LLVM_DIR:-/usr/lib/llvm-14}
BUILD_ROOT=$WORK_DIR/build
TMP_DIR=$WORK_DIR/tmp/distance
LOG_DIR=$WORK_DIR/logs
BIN_DIR=$WORK_DIR/bin
SOURCE_TARGETS_FILE=${SOURCE_TARGETS_FILE:-$WORK_DIR/targets/mysql_rq2_targets.txt}
TARGETS_FILE=$TMP_DIR/BBtargets.txt
BUILD_JOBS=${BUILD_JOBS:-1}

mkdir -p "$BUILD_ROOT" "$TMP_DIR" "$LOG_DIR" "$BIN_DIR"

if [ ! -x "$AFLGO_INSTRUMENT_DIR/aflgo-clang" ]; then
  echo "missing AFLGo compiler: $AFLGO_INSTRUMENT_DIR/aflgo-clang" >&2
  exit 1
fi

if [ ! -x "$LLVM_DIR/bin/clang" ] || [ ! -x "$LLVM_DIR/bin/clang++" ]; then
  echo "missing LLVM 14 clang toolchain under $LLVM_DIR" >&2
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

if [ ! -f "$MYSQL_SRC/CMakeLists.txt" ]; then
  echo "missing MySQL source tree: $MYSQL_SRC" >&2
  exit 1
fi

if [ ! -s "$SOURCE_TARGETS_FILE" ]; then
  echo "missing AFLGo target list: $SOURCE_TARGETS_FILE" >&2
  echo "run $WORK_DIR/prepare_rq2_targets.sh first, or set SOURCE_TARGETS_FILE explicitly" >&2
  exit 1
fi
cp "$SOURCE_TARGETS_FILE" "$TARGETS_FILE"

mkdir -p /usr/lib/bfd-plugins
if [ -f "$LLVM_DIR/lib/LLVMgold.so" ]; then
  ln -sf "$LLVM_DIR/lib/LLVMgold.so" /usr/lib/bfd-plugins/LLVMgold.so
fi
if [ -f "$LLVM_DIR/lib/libLTO.so" ]; then
  ln -sf "$LLVM_DIR/lib/libLTO.so" /usr/lib/bfd-plugins/libLTO.so
fi

unset AFLGO
export AFL_CC=${AFL_CC:-$LLVM_DIR/bin/clang}
export AFL_CXX=${AFL_CXX:-$LLVM_DIR/bin/clang++}
export CC="$AFLGO_INSTRUMENT_DIR/aflgo-clang"
export CXX="$AFLGO_INSTRUMENT_DIR/aflgo-clang++"
export PATH="$LLVM_DIR/bin:$PATH"
export AFL_QUIET=1
export AFL_DONT_OPTIMIZE=1

rm -rf "$BUILD_ROOT/mysql-preprocess" "$BUILD_ROOT/mysql-instrumented" "$BUILD_ROOT/mysql-preprocess-bc"
rm -rf "$TMP_DIR/dot-files"
rm -f "$TMP_DIR"/BBnames.txt "$TMP_DIR"/BBcalls.txt "$TMP_DIR"/Fnames.txt \
      "$TMP_DIR"/Ftargets.txt "$TMP_DIR"/distance.cfg.txt "$TMP_DIR"/step*.log \
      "$TMP_DIR"/state-fast "$TMP_DIR"/callgraph.distance.txt

BOOST_DIR=${BOOST_DIR:-$MYSQL_SRC/extra/boost/boost_1_87_0}

COMMON_CMAKE=(
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
  -DWITH_BOOST="$BOOST_DIR"
  -DDOWNLOAD_BOOST=0
  -DWITH_UNIT_TESTS=OFF
  -DWITH_ROUTER=OFF
  -DWITH_NDB=OFF
  -DWITH_EXAMPLE_STORAGE_ENGINE=OFF
  -DWITH_FEDERATED_STORAGE_ENGINE=OFF
  -DWITH_BLACKHOLE_STORAGE_ENGINE=OFF
  -DFORCE_UNSUPPORTED_COMPILER=1
)

echo "[1/5] AFLGo preprocessing CMake configure"
mkdir -p "$BUILD_ROOT/mysql-preprocess"
(
  cd "$BUILD_ROOT/mysql-preprocess"
  cmake "$MYSQL_SRC" "${COMMON_CMAKE[@]}" \
    -DCMAKE_C_COMPILER="$CC" \
    -DCMAKE_CXX_COMPILER="$CXX" \
    -DCMAKE_C_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
    -DCMAKE_CXX_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
    -DCMAKE_EXE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
    -DCMAKE_SHARED_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
    -DCMAKE_MODULE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps"
)

echo "[2/5] AFLGo preprocessing build target mysqld"
cmake --build "$BUILD_ROOT/mysql-preprocess" --target mysqld -j "$BUILD_JOBS"

echo "[3/5] Preparing AFLGo distance inputs"
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

mkdir -p "$BUILD_ROOT/mysql-preprocess-bc"
MYSQLD_PRE=$(find "$BUILD_ROOT/mysql-preprocess" -type f -name mysqld -perm -111 | head -n 1)
MYSQLD_BC=$(find "$BUILD_ROOT/mysql-preprocess" -type f -name 'mysqld.0.0.*.bc' | head -n 1)
if [ -z "$MYSQLD_PRE" ] || [ -z "$MYSQLD_BC" ]; then
  echo "missing preprocessing mysqld or mysqld bytecode" >&2
  echo "mysqld=$MYSQLD_PRE"
  echo "mysqld_bc=$MYSQLD_BC"
  exit 1
fi
ln -sf "$MYSQLD_PRE" "$BUILD_ROOT/mysql-preprocess-bc/mysqld"
ln -sf "$MYSQLD_BC" "$BUILD_ROOT/mysql-preprocess-bc/$(basename "$MYSQLD_BC")"

echo "[4/5] Computing AFLGo distances"
python3 "$AFLGO_DIR/distance/gen_distance_fast.py" "$BUILD_ROOT/mysql-preprocess-bc" "$TMP_DIR" mysqld
if [ ! -s "$TMP_DIR/distance.cfg.txt" ]; then
  echo "distance.cfg.txt is empty; see $TMP_DIR/step*.log" >&2
  exit 1
fi

echo "[5/5] Final distance-instrumented MySQL build"
mkdir -p "$BUILD_ROOT/mysql-instrumented"
(
  cd "$BUILD_ROOT/mysql-instrumented"
  cmake "$MYSQL_SRC" "${COMMON_CMAKE[@]}" \
    -DCMAKE_C_COMPILER="$CC" \
    -DCMAKE_CXX_COMPILER="$CXX" \
    -DCMAKE_C_FLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt" \
    -DCMAKE_CXX_FLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt"
)
cmake --build "$BUILD_ROOT/mysql-instrumented" --target mysqld mysql -j "$BUILD_JOBS"

MYSQLD_FINAL=$(find "$BUILD_ROOT/mysql-instrumented" -type f -name mysqld -perm -111 | head -n 1)
MYSQL_CLIENT_FINAL=$(find "$BUILD_ROOT/mysql-instrumented" -type f -name mysql -perm -111 | head -n 1)
if [ -z "$MYSQLD_FINAL" ]; then
  echo "missing final instrumented mysqld" >&2
  exit 1
fi

cp "$MYSQLD_FINAL" "$BIN_DIR/mysqld_aflgo"
chmod +x "$BIN_DIR/mysqld_aflgo"
if [ -n "$MYSQL_CLIENT_FINAL" ]; then
  cp "$MYSQL_CLIENT_FINAL" "$BIN_DIR/mysql_client"
  chmod +x "$BIN_DIR/mysql_client"
fi

"$CC" \
  -DNDEBUG -g -O1 -flegacy-pass-manager -distance="$TMP_DIR/distance.cfg.txt" \
  "$WORK_DIR/mysql_bootstrap_wrapper.c" \
  -o "$BIN_DIR/mysql_bootstrap_wrapper_aflgo"
chmod +x "$BIN_DIR/mysql_bootstrap_wrapper_aflgo"

{
  echo "mysqld_binary=$BIN_DIR/mysqld_aflgo"
  echo "bootstrap_wrapper=$BIN_DIR/mysql_bootstrap_wrapper_aflgo"
  [ -n "$MYSQL_CLIENT_FINAL" ] && echo "mysql_client=$BIN_DIR/mysql_client"
  echo "source_targets_file=$SOURCE_TARGETS_FILE"
  echo "targets_file=$TARGETS_FILE"
  echo "targets_count=$(wc -l < "$TARGETS_FILE")"
  echo "targets_sha256=$(sha256sum "$TARGETS_FILE" | awk '{print $1}')"
  echo "distance_file=$TMP_DIR/distance.cfg.txt"
  echo "bb_count=$(wc -l < "$TMP_DIR/BBnames.txt")"
  echo "ftarget_count=$(wc -l < "$TMP_DIR/Ftargets.txt")"
  echo "distance_count=$(wc -l < "$TMP_DIR/distance.cfg.txt")"
  echo "mysql_source=$(cd "$MYSQL_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "boost_dir=$BOOST_DIR"
  echo "build_jobs=$BUILD_JOBS"
  echo "llvm_dir=$LLVM_DIR"
  echo "aflgo_instrument_dir=$AFLGO_INSTRUMENT_DIR"
  echo "afl_cc=$AFL_CC"
  echo "afl_cxx=$AFL_CXX"
  echo "build_command=$0"
  echo "preprocess_mysqld=$MYSQLD_PRE"
  echo "preprocess_mysqld_bc=$MYSQLD_BC"
  echo "final_mysqld=$MYSQLD_FINAL"
} | tee "$LOG_DIR/mysql_build_summary.txt"
