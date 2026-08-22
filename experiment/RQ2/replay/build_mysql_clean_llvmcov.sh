#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
OUT_DIR="$SCRIPT_DIR/output"
DOCKERFILE="$SCRIPT_DIR/llvmcov_build/mysql_clean/Dockerfile"

IMAGE=${RQ2_MYSQL_CLEAN_IMAGE:-griffin_mysql_clean_llvmcov}
MAKE_JOBS=${MAKE_JOBS:-4}
MYSQL_SOURCE_DIR=${MYSQL_SOURCE_DIR:-$PROJECT_ROOT/sources/mysql}
TS=$(date -u +%Y%m%d_%H%M%S)
LOG="$OUT_DIR/build_${IMAGE}_${TS}.log"
CTX=$(mktemp -d /tmp/rq2_mysql_clean_llvmcov_ctx_${TS}_XXXXXX)

mkdir -p "$OUT_DIR"

cleanup() {
  rm -rf "$CTX"
}
trap cleanup EXIT

[[ -d "$MYSQL_SOURCE_DIR" ]] || { echo "missing mysql source dir: $MYSQL_SOURCE_DIR" >&2; exit 2; }
[[ -f "$MYSQL_SOURCE_DIR/CMakeLists.txt" ]] || { echo "invalid mysql source dir: $MYSQL_SOURCE_DIR" >&2; exit 2; }

cp "$DOCKERFILE" "$CTX/Dockerfile"
mkdir -p "$CTX/mysql_src"
( cd "$MYSQL_SOURCE_DIR" && tar --exclude=.git -cf - . ) | ( cd "$CTX/mysql_src" && tar -xf - )

echo "image=$IMAGE"
echo "make_jobs=$MAKE_JOBS"
echo "mysql_source_dir=$MYSQL_SOURCE_DIR"
echo "mysql_source_head=$(git -C "$MYSQL_SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
echo "log=$LOG"
echo "context=$CTX"

docker build   --build-arg MAKE_JOBS="$MAKE_JOBS"   -t "$IMAGE"   -f "$CTX/Dockerfile"   "$CTX" 2>&1 | tee "$LOG"

docker run --rm --entrypoint /bin/bash "$IMAGE" -lc   ''command -v llvm-profdata-18 && command -v llvm-cov-18 && test -x "$MYSQLD_BINARY" && test -x "$MYSQL_CLIENT" && strings "$MYSQLD_BINARY" | grep -q __llvm_prf && "$MYSQLD_BINARY" --version''

echo "$IMAGE"
