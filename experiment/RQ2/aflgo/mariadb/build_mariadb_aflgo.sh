#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MARIADB_SRC=${MARIADB_SRC:-/root/SQLeek/sources/mariadb}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mariadb}
AFLGO_INSTRUMENT_DIR=${AFLGO_INSTRUMENT_DIR:-$AFLGO_DIR/instrument_llvm14}
LLVM_DIR=${LLVM_DIR:-/usr/lib/llvm-14}
BUILD_ROOT=$WORK_DIR/build
TMP_DIR=$WORK_DIR/tmp/distance
LOG_DIR=$WORK_DIR/logs
BIN_DIR=$WORK_DIR/bin
SOURCE_TARGETS_FILE=${SOURCE_TARGETS_FILE:-$WORK_DIR/targets/mariadb_rq2_targets.txt}
TARGETS_FILE=$TMP_DIR/BBtargets.txt
BUILD_JOBS=${BUILD_JOBS:-1}
FINAL_PREFIX=$BUILD_ROOT/mariadb-install

mkdir -p "$BUILD_ROOT" "$TMP_DIR" "$LOG_DIR" "$BIN_DIR"

prepare_local_deps() {
  local fmt_zip="$WORK_DIR/deps/fmt-12.1.0.zip"
  local fmt_dir="$WORK_DIR/deps/fmt-12.1.0"
  if [ ! -f "$fmt_dir/include/fmt/core.h" ]; then
    [ -f "$fmt_zip" ] || { echo "missing local fmt archive: $fmt_zip" >&2; exit 1; }
    mkdir -p "$WORK_DIR/deps"
    (cd "$WORK_DIR/deps" && rm -rf fmt-12.1.0 && unzip -q fmt-12.1.0.zip)
  fi
}
prepare_local_deps
if [ ! -x "$AFLGO_INSTRUMENT_DIR/aflgo-clang" ]; then echo "missing AFLGo compiler: $AFLGO_INSTRUMENT_DIR/aflgo-clang" >&2; exit 1; fi
if [ ! -x "$LLVM_DIR/bin/clang" ] || [ ! -x "$LLVM_DIR/bin/clang++" ]; then echo "missing LLVM toolchain under $LLVM_DIR" >&2; exit 1; fi
if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ] && [ -x "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" ]; then
  ln -sf "$AFLGO_DIR/distance/distance_calculator/build/distance.bin" "$AFLGO_DIR/distance/distance_calculator/distance.bin"
fi
if [ ! -x "$AFLGO_DIR/distance/distance_calculator/distance.bin" ]; then echo "missing AFLGo distance calculator" >&2; exit 1; fi
if [ ! -f "$MARIADB_SRC/CMakeLists.txt" ]; then echo "missing MariaDB source: $MARIADB_SRC" >&2; exit 1; fi
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

rm -rf "$BUILD_ROOT/mariadb-preprocess" "$BUILD_ROOT/mariadb-instrumented" "$BUILD_ROOT/mariadb-preprocess-bc" "$FINAL_PREFIX"
rm -rf "$TMP_DIR/dot-files"
rm -f "$TMP_DIR"/BBnames.txt "$TMP_DIR"/BBcalls.txt "$TMP_DIR"/Fnames.txt "$TMP_DIR"/Ftargets.txt \
      "$TMP_DIR"/distance.cfg.txt "$TMP_DIR"/step*.log "$TMP_DIR"/state-fast "$TMP_DIR"/callgraph.distance.txt

COMMON_CMAKE=(
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
  -DCMAKE_INSTALL_PREFIX="$FINAL_PREFIX"
  -DCONNECT_WITH_JDBC=OFF
  -DPLUGIN_CONNECT=NO
  -DPLUGIN_ROCKSDB=NO
  -DPLUGIN_COLUMNSTORE=NO
  -DPLUGIN_TOKUDB=NO
  -DPLUGIN_MROONGA=NO
  -DPLUGIN_S3=NO
  -DWITH_LIBFMT=system
  -DLIBFMT_INCLUDE_DIR="$WORK_DIR/deps/fmt-12.1.0/include"
  -DWITH_UNIT_TESTS=OFF
  -DWITH_WSREP=OFF
  -DFORCE_UNSUPPORTED_COMPILER=1
)

configure_build() {
  local out_dir=$1
  local mode=$2
  mkdir -p "$out_dir"
  if [ "$mode" = preprocess ]; then
    cmake -S "$MARIADB_SRC" -B "$out_dir" "${COMMON_CMAKE[@]}" \
      -DCMAKE_C_COMPILER="$CC" \
      -DCMAKE_CXX_COMPILER="$CXX" \
      -DCMAKE_C_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
      -DCMAKE_CXX_FLAGS="-g -O0 -flegacy-pass-manager -fno-inline -targets=$TARGETS_FILE -outdir=$TMP_DIR -flto" \
      -DCMAKE_EXE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
      -DCMAKE_SHARED_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps" \
      -DCMAKE_MODULE_LINKER_FLAGS="-flto -fuse-ld=gold -Wl,-plugin-opt=save-temps"
  else
    cmake -S "$MARIADB_SRC" -B "$out_dir" "${COMMON_CMAKE[@]}" \
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


seed_external_archives() {
  local build_dir=$1
  local pcre_zip="$WORK_DIR/deps/pcre2-10.47.zip"
  if [ -f "$pcre_zip" ]; then
    mkdir -p "$build_dir/extra/pcre2/src"
    cp -p "$pcre_zip" "$build_dir/extra/pcre2/src/pcre2-10.47.zip"
  fi
}

echo "[1/5] AFLGo preprocessing CMake configure"
configure_build "$BUILD_ROOT/mariadb-preprocess" preprocess
seed_external_archives "$BUILD_ROOT/mariadb-preprocess"

echo "[2/5] AFLGo preprocessing build target mariadbd"
build_target_or_die "$BUILD_ROOT/mariadb-preprocess" mariadbd

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
mkdir -p "$BUILD_ROOT/mariadb-preprocess-bc"
MARIADBD_PRE=$(find_exe "$BUILD_ROOT/mariadb-preprocess" mariadbd)
MARIADBD_BC=$(find "$BUILD_ROOT/mariadb-preprocess" -type f -name 'mariadbd.0.0.*.bc' | head -n 1)
if [ -z "$MARIADBD_PRE" ] || [ -z "$MARIADBD_BC" ]; then
  echo "missing preprocessing mariadbd or mariadbd bytecode" >&2
  echo "mariadbd=$MARIADBD_PRE"
  echo "mariadbd_bc=$MARIADBD_BC"
  exit 1
fi
ln -sf "$MARIADBD_PRE" "$BUILD_ROOT/mariadb-preprocess-bc/mariadbd"
ln -sf "$MARIADBD_BC" "$BUILD_ROOT/mariadb-preprocess-bc/$(basename "$MARIADBD_BC")"

echo "[4/5] Computing AFLGo distances"
python3 "$AFLGO_DIR/distance/gen_distance_fast.py" "$BUILD_ROOT/mariadb-preprocess-bc" "$TMP_DIR" mariadbd
if [ ! -s "$TMP_DIR/distance.cfg.txt" ]; then echo "distance.cfg.txt is empty; see $TMP_DIR/step*.log" >&2; exit 1; fi

echo "[5/5] Final distance-instrumented MariaDB build"
configure_build "$BUILD_ROOT/mariadb-instrumented" final
seed_external_archives "$BUILD_ROOT/mariadb-instrumented"
build_target_or_die "$BUILD_ROOT/mariadb-instrumented" mariadbd
cmake --build "$BUILD_ROOT/mariadb-instrumented" --target mariadb -j "$BUILD_JOBS" || true
cmake --build "$BUILD_ROOT/mariadb-instrumented" --target mariadb-install-db -j "$BUILD_JOBS" || true

MARIADBD_FINAL=$(find_exe "$BUILD_ROOT/mariadb-instrumented" mariadbd)
MARIADB_CLIENT_FINAL=$(find_exe "$BUILD_ROOT/mariadb-instrumented" mariadb)
INSTALL_DB_FINAL=$(find_exe "$BUILD_ROOT/mariadb-instrumented" mariadb-install-db)
if [ -z "$MARIADBD_FINAL" ]; then echo "missing final instrumented mariadbd" >&2; exit 1; fi
cp "$MARIADBD_FINAL" "$BIN_DIR/mariadbd_aflgo"
chmod +x "$BIN_DIR/mariadbd_aflgo"
[ -n "$MARIADB_CLIENT_FINAL" ] && cp "$MARIADB_CLIENT_FINAL" "$BIN_DIR/mariadb_client" && chmod +x "$BIN_DIR/mariadb_client"
[ -n "$INSTALL_DB_FINAL" ] && cp "$INSTALL_DB_FINAL" "$BIN_DIR/mariadb_install_db" && chmod +x "$BIN_DIR/mariadb_install_db"

"$CC" -DNDEBUG -g -O1 -flegacy-pass-manager -distance="$TMP_DIR/distance.cfg.txt" \
  "$WORK_DIR/mariadb_bootstrap_wrapper.c" -o "$BIN_DIR/mariadb_bootstrap_wrapper_aflgo"
chmod +x "$BIN_DIR/mariadb_bootstrap_wrapper_aflgo"

{
  echo "mariadbd_binary=$BIN_DIR/mariadbd_aflgo"
  echo "bootstrap_wrapper=$BIN_DIR/mariadb_bootstrap_wrapper_aflgo"
  [ -n "$MARIADB_CLIENT_FINAL" ] && echo "mariadb_client=$BIN_DIR/mariadb_client"
  [ -n "$INSTALL_DB_FINAL" ] && echo "mariadb_install_db=$BIN_DIR/mariadb_install_db"
  echo "source_targets_file=$SOURCE_TARGETS_FILE"
  echo "targets_file=$TARGETS_FILE"
  echo "targets_count=$(wc -l < "$TARGETS_FILE")"
  echo "targets_sha256=$(sha256sum "$TARGETS_FILE" | awk '{print $1}')"
  echo "distance_file=$TMP_DIR/distance.cfg.txt"
  echo "bb_count=$(wc -l < "$TMP_DIR/BBnames.txt")"
  [ -f "$TMP_DIR/Ftargets.txt" ] && echo "ftarget_count=$(wc -l < "$TMP_DIR/Ftargets.txt")"
  echo "distance_count=$(wc -l < "$TMP_DIR/distance.cfg.txt")"
  echo "mariadb_source=$(cd "$MARIADB_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
  echo "build_jobs=$BUILD_JOBS"
  echo "llvm_dir=$LLVM_DIR"
  echo "aflgo_instrument_dir=$AFLGO_INSTRUMENT_DIR"
  echo "build_command=$0"
  echo "preprocess_mariadbd=$MARIADBD_PRE"
  echo "preprocess_mariadbd_bc=$MARIADBD_BC"
  echo "final_mariadbd=$MARIADBD_FINAL"
} | tee "$LOG_DIR/mariadb_build_summary.txt"
