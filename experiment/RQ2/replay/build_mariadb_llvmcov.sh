#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OUT_DIR="$SCRIPT_DIR/output"
DOCKERFILE_SRC="$SCRIPT_DIR/llvmcov_build/mariadb/Dockerfile"

IMAGE=${RQ2_MARIADB_IMAGE:-griffin_mariadb_llvmcov}
MAKE_JOBS=${MAKE_JOBS:-24}
MARIADB_TAG=${MARIADB_TAG:-mariadb-12.3.2}
DFUZZ_MARIADB_DIR=${DFUZZ_MARIADB_DIR:-/root/dfuzz-griffin/docker/mariadb}
GRIFFIN_DOCKER_PARAMS=${GRIFFIN_DOCKER_PARAMS:-"--network host"}

TS=$(date -u +%Y%m%d_%H%M%S)
LOG="$OUT_DIR/build_${IMAGE}_${TS}.log"
CTX="/tmp/rq2_mariadb_llvmcov_ctx_${TS}"

mkdir -p "$OUT_DIR"
rm -rf "$CTX"
mkdir -p "$CTX"

cleanup() {
  rm -rf "$CTX"
}
trap cleanup EXIT

for image in griffin_base:latest griffin_base_ex:latest; do
  docker image inspect "$image" >/dev/null
done

for path in \
  "$DOCKERFILE_SRC" \
  "$DFUZZ_MARIADB_DIR/configs" \
  "$DFUZZ_MARIADB_DIR/libraries" \
  "$DFUZZ_MARIADB_DIR/scripts"; do
  [[ -e "$path" ]] || { echo "missing required path: $path" >&2; exit 2; }
done

SSH_SPEC=${DOCKER_SSH_SPEC:-}
if [[ -z "$SSH_SPEC" ]]; then
  if [[ -n "${SSH_AUTH_SOCK:-}" ]]; then
    SSH_SPEC=default="$SSH_AUTH_SOCK"
  elif [[ -f "$HOME/.ssh/id_ed25519" ]]; then
    SSH_SPEC=default="$HOME/.ssh/id_ed25519"
  elif [[ -f "$HOME/.ssh/id_rsa" ]]; then
    SSH_SPEC=default="$HOME/.ssh/id_rsa"
  else
    echo "missing SSH_AUTH_SOCK or ~/.ssh/id_ed25519/id_rsa for BuildKit --ssh" >&2
    exit 2
  fi
fi

cp "$DOCKERFILE_SRC" "$CTX/Dockerfile"
cp -a "$DFUZZ_MARIADB_DIR/configs" "$CTX/configs"
cp -a "$DFUZZ_MARIADB_DIR/libraries" "$CTX/libraries"
cp -a "$DFUZZ_MARIADB_DIR/scripts" "$CTX/scripts"

echo "image=$IMAGE"
echo "make_jobs=$MAKE_JOBS"
echo "mariadb_tag=$MARIADB_TAG"
echo "ssh_spec=$SSH_SPEC"
echo "docker_params=$GRIFFIN_DOCKER_PARAMS"
echo "log=$LOG"
echo "context=$CTX"

DOCKER_BUILDKIT=1 docker build \
  --ssh "$SSH_SPEC" \
  $GRIFFIN_DOCKER_PARAMS \
  --build-arg MAKE_JOBS="$MAKE_JOBS" \
  --build-arg MARIADB_TAG="$MARIADB_TAG" \
  -t "$IMAGE" \
  -f "$CTX/Dockerfile" \
  "$CTX" 2>&1 | tee "$LOG"

docker run --rm --entrypoint /bin/bash "$IMAGE" -lc \
  'command -v llvm-profdata-12 && command -v llvm-cov-12 && \
   test -x "$MARIADBD_BINARY" && \
   test -x "$MARIADB_CLIENT" && \
   test -x /root/bin_original/usr/local/mysql/scripts/mysql_install_db && \
   strings "$MARIADBD_BINARY" | grep -q __llvm_prf && \
   "$MARIADBD_BINARY" --version'

echo "$IMAGE"
