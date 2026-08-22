#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MONETDB_SRC=${MONETDB_SRC:-/root/SQLeek/sources/monetdb}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/monetdb}
AFLGO_INSTRUMENT_DIR=${AFLGO_INSTRUMENT_DIR:-$AFLGO_DIR/instrument_llvm14}
LLVM_DIR=${LLVM_DIR:-/usr/lib/llvm-14}
BUILD_ROOT=$WORK_DIR/build
TMP_DIR=$WORK_DIR/tmp/distance
LOG_DIR=$WORK_DIR/logs
BIN_DIR=$WORK_DIR/bin
SOURCE_TARGETS_FILE=${SOURCE_TARGETS_FILE:-$WORK_DIR/targets/monetdb_rq2_targets.txt}
TARGETS_FILE=$TMP_DIR/BBtargets.txt
BUILD_JOBS=${BUILD_JOBS:-1}
FINAL_PREFIX=$BUILD_ROOT/monetdb-install

mkdir -p "$BUILD_ROOT" "$TMP_DIR" "$LOG_DIR" "$BIN_DIR"

if [ ! -x "$AFLGO_INSTRUMENT_DIR/aflgo-clang" ]; then echo "missing AFLGo compiler: $AFLGO_INSTRUMENT_DIR/aflgo-clang" >&2; exit 1; fi
if [ ! -x "$LLVM_DIR/bin/clang" ] || [ ! -x "$LLVM_DIR/bin/clang++" ]; then echo "missing LLVM toolchain under $LLVM_DIR" >&2; exit 1; fi
if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ] && [ -x "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" ]; then
  ln -sf "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" "$AFLGO_DIR/distance/distance_calculator/distance.bin"
fi
if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ]; then echo "missing AFLGo distance calculator" >&2; exit 1; fi
if [ ! -f "$MONETDB_SRC/CMakeLists.txt" ]; then echo "missing MonetDB source: $MONETDB_SRC" >&2; exit 1; fi
if [ ! -s "$SOURCE_TARGETS_FILE" ]; then echo "missing AFLGo target list: $SOURCE_TARGETS_FILE" >&2; exit 1; fi
cp "$SOURCE_TARGETS_FILE" "$TARGETS_FILE"

mkdir -p /usr/lib/bfd-plugins
[ -f "$LLVM_DIR/lib/LLVMgold.so" ] && ln -sf "$LLVM_DIR/lib/LLVMgold.so" /usr/lib/bfd-plugins/LLVMgold.so
[ -f "$LLVM_DIR/lib/libLTO.so" ] && ln -sf "$LLVM_DIR/lib/libLTO.so" /usr/lib/bfd-plugins/libLTO.so

unset AFLGO
export AFL_CC=${AFL_CC:-$LLVM_DIR/bin/clang}
export AFL_CXX=${AFL_CXX:-$LLVM_DIR/bin/clang++}
export CC="$AFLGO_INSTRUMENT_DIR/aflgo-clang"
export CXX="$AFLGO_INSTRUMENT_DIR/aflgo-clang++"
export PATH="$LLVM_DIR/bin:$PATH"
export AFL_QUIET=1
export AFL_DONT_OPTIMIZE=1

rm -rf "$BUILD_ROOT/monetdb-preprocess" "$BUILD_ROOT/monetdb-instrumented" "$BUILD_ROOT/monetdb-preprocess-bc" "$FINAL_PREFIX"
rm -rf "$TMP_DIR/dot-files"
rm -f "$TMP_DIR"/BBnames.txt "$TMP_DIR"/BBcalls.txt "$TMP_DIR"/Fnames.txt "$TMP_DIR"/Ftargets.txt \
      "$TMP_DIR"/distance.cfg.txt "$TMP_DIR"/step*.log "$TMP_DIR"/state-fast "$TMP_DIR"/callgraph.distance.txt

COMMON_CMAKE=(
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
  -DCMAKE_INSTALL_PREFIX="$FINAL_PREFIX"
  -DASSERT=OFF
  -DSTRICT=OFF
  -DWITH_TESTING=OFF
  -DBUILD_TESTING=OFF
  -DWITH_READLINE=OFF
)

configure_build() {
  local out_dir=$1
  local mode=$2
  mkdir -p "$out_dir"
  if [ "$mode" = preprocess ]; then
    cmake -S "$MONETDB_SRC" -B "$out_dir" "${COMMON_CMAKE[@]}" \
      -DCMAKE_C_COMPILER="$CC" \
      -DCMAKE_CXX_COMPILER="$CXX" \
      -DCMAKE_C_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
      -DCMAKE_CXX_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
      -DCMAKE_EXE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
      -DCMAKE_SHARED_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
      -DCMAKE_MODULE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps"
  else
    cmake -S "$MONETDB_SRC" -B "$out_dir" "${COMMON_CMAKE[@]}" \
      -DCMAKE_C_COMPILER="$CC" \
      -DCMAKE_CXX_COMPILER="$CXX" \
      -DCMAKE_C_FLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt" \
      -DCMAKE_CXX_FLAGS="-g -O1 -flegacy-pass-manager -distance=$TMP_DIR/distance.cfg.txt"
  fi
}

build_target_or_die() {
  local build_dir=$1
  local target=$2
  cmake --build "$build_dir" --target "$target" -j "$BUILD_JOBS"
}

find_exe() {
  local root=$1
  local name=$2
  find "$root" -type f -name "$name" -perm -111 | head -n 1
}

echo "[1/5] AFLGo preprocessing CMake configure"
configure_build "$BUILD_ROOT/monetdb-preprocess" preprocess

echo "[2/5] AFLGo preprocessing build mserver5 and mclient"
build_target_or_die "$BUILD_ROOT/monetdb-preprocess" mserver5
cmake --build "$BUILD_ROOT/monetdb-preprocess" --target mclient -j "$BUILD_JOBS" || true

echo "[3/5] Preparing AFLGo distance inputs"
if [ ! -s "$TMP_DIR/BBnames.txt" ]; then echo "AFLGo did not produce BBnames.txt" >&2; exit 1; fi
grep -v '^$' "$TMP_DIR/BBnames.txt" | rev | cut -d: -f2- | rev | sort -u > "$TMP_DIR/BBnames.clean"
mv "$TMP_DIR/BBnames.clean" "$TMP_DIR/BBnames.txt"
if [ -s "$TMP_DIR/BBcalls.txt" ]; then
  grep -Ev '^[^,]*$|^([^,]*,){2,}[^,]*$' "$TMP_DIR/BBcalls.txt" | sort -u > "$TMP_DIR/BBcalls.clean" || true
  mv "$TMP_DIR/BBcalls.clean" "$TMP_DIR/BBcalls.txt"
else
  : > "$TMP_DIR/BBcalls.txt"
fi
mkdir -p "$BUILD_ROOT/monetdb-preprocess-bc"
MSERVER_PRE=$(find "$BUILD_ROOT/monetdb-preprocess" -xtype f -path '*/tools/mserver/mserver5' | head -n 1)
[ -z "$MSERVER_PRE" ] && MSERVER_PRE=$(find "$BUILD_ROOT/monetdb-preprocess" -type f -path '*/tools/mserver/mserver5-*' -perm -111 | head -n 1)
MSERVER_BC=$(find "$BUILD_ROOT/monetdb-preprocess" -type f -path '*/tools/mserver/mserver5*.0.0.*.bc' | head -n 1)
if [ -z "$MSERVER_PRE" ] || [ -z "$MSERVER_BC" ]; then
  echo "missing preprocessing mserver5 or mserver5 bytecode" >&2
  echo "mserver5=$MSERVER_PRE"
  echo "mserver5_bc=$MSERVER_BC"
  find "$BUILD_ROOT/monetdb-preprocess" -type f -name '*.0.0.*.bc' | sort | tail -100 >&2
  exit 1
fi
ln -sf "$MSERVER_PRE" "$BUILD_ROOT/monetdb-preprocess-bc/mserver5"
ln -sf "$MSERVER_BC" "$BUILD_ROOT/monetdb-preprocess-bc/$(basename "$MSERVER_BC")"
ln -sf "$MSERVER_BC" "$BUILD_ROOT/monetdb-preprocess-bc/mserver5.0.0.preopt.bc"

echo "[4/5] Computing AFLGo distances"
python3 "$AFLGO_DIR/distance/gen_distance_fast.py" "$BUILD_ROOT/monetdb-preprocess-bc" "$TMP_DIR" mserver5
if [ ! -s "$TMP_DIR/distance.cfg.txt" ]; then echo "distance.cfg.txt is empty; see $TMP_DIR/step*.log" >&2; exit 1; fi

echo "[5/5] Final distance-instrumented MonetDB build"
configure_build "$BUILD_ROOT/monetdb-instrumented" final
build_target_or_die "$BUILD_ROOT/monetdb-instrumented" mserver5
cmake --build "$BUILD_ROOT/monetdb-instrumented" --target mclient -j "$BUILD_JOBS" || true
cmake --install "$BUILD_ROOT/monetdb-instrumented" --prefix "$FINAL_PREFIX" || true

MSERVER_FINAL=$(find "$BUILD_ROOT/monetdb-instrumented" -xtype f -path '*/tools/mserver/mserver5' | head -n 1)
[ -z "$MSERVER_FINAL" ] && MSERVER_FINAL=$(find "$BUILD_ROOT/monetdb-instrumented" -type f -path '*/tools/mserver/mserver5-*' -perm -111 | head -n 1)
MCLIENT_FINAL=$(find "$BUILD_ROOT/monetdb-instrumented" -xtype f -path '*/clients/mapiclient/mclient' | head -n 1)
[ -z "$MCLIENT_FINAL" ] && MCLIENT_FINAL=$(find "$BUILD_ROOT/monetdb-instrumented" -type f -path '*/clients/mapiclient/mclient-*' -perm -111 | head -n 1)
[ -z "$MSERVER_FINAL" ] && MSERVER_FINAL=$(find_exe "$FINAL_PREFIX" mserver5 || true)
[ -z "$MCLIENT_FINAL" ] && MCLIENT_FINAL=$(find_exe "$FINAL_PREFIX" mclient || true)
if [ -z "$MSERVER_FINAL" ]; then echo "missing final instrumented mserver5" >&2; exit 1; fi
cp "$MSERVER_FINAL" "$BIN_DIR/mserver5_aflgo"
chmod +x "$BIN_DIR/mserver5_aflgo"
if [ -n "$MCLIENT_FINAL" ]; then cp "$MCLIENT_FINAL" "$BIN_DIR/mclient_aflgo"; chmod +x "$BIN_DIR/mclient_aflgo"; fi

{
  echo "mserver5_binary=$BIN_DIR/mserver5_aflgo"
  [ -n "$MCLIENT_FINAL" ] && echo "mclient_binary=$BIN_DIR/mclient_aflgo"
  echo "install_prefix=$FINAL_PREFIX"
  echo "source_targets_file=$SOURCE_TARGETS_FILE"
  echo "targets_file=$TARGETS_FILE"
  echo "targets_count=$(wc -l < "$TARGETS_FILE")"
  echo "targets_sha256=$(sha256sum "$TARGETS_FILE" | awk '{print $1}')"
  echo "distance_file=$TMP_DIR/distance.cfg.txt"
  echo "bb_count=$(wc -l < "$TMP_DIR/BBnames.txt")"
  [ -f "$TMP_DIR/Ftargets.txt" ] && echo "ftarget_count=$(wc -l < "$TMP_DIR/Ftargets.txt")"
  echo "distance_count=$(wc -l < "$TMP_DIR/distance.cfg.txt")"
  echo "monetdb_source=$(cd "$MONETDB_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "build_jobs=$BUILD_JOBS"
  echo "llvm_dir=$LLVM_DIR"
  echo "aflgo_instrument_dir=$AFLGO_INSTRUMENT_DIR"
  echo "build_command=$0"
  echo "preprocess_mserver5=$MSERVER_PRE"
  echo "preprocess_mserver5_bc=$MSERVER_BC"
  echo "final_mserver5=$MSERVER_FINAL"
  [ -n "$MCLIENT_FINAL" ] && echo "final_mclient=$MCLIENT_FINAL"
} | tee "$LOG_DIR/monetdb_build_summary.txt"
