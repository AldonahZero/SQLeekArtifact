#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$STAGE_DIR"

usage() {
  echo "Usage: ./docker/build.sh mysql|mariadb|postgresql|monetdb|all" >&2
}

dbms="${1:-}"
[ -n "$dbms" ] || { usage; exit 2; }

case "$dbms" in
  postgres) dbms="postgresql" ;;
  mysql|mariadb|postgresql|monetdb|all) ;;
  *) usage; exit 2 ;;
esac

tag="${SQLEEK_IMAGE_TAG:-$(git -C /root/SQLeek rev-parse --short HEAD 2>/dev/null || date +%Y%m%d)}"
base_image="${BASE_IMAGE:-ubuntu:22.04}"

ssh_args=()
if [ -n "${SQLEEK_DOCKER_SSH:-}" ]; then
  ssh_args=(--ssh "$SQLEEK_DOCKER_SSH")
elif [ -n "${SSH_AUTH_SOCK:-}" ]; then
  ssh_args=(--ssh default)
elif [ -r /root/.ssh/id_ed25519 ]; then
  ssh_args=(--ssh default=/root/.ssh/id_ed25519)
elif [ -r /root/.ssh/id_rsa ]; then
  ssh_args=(--ssh default=/root/.ssh/id_rsa)
elif [ -r "$HOME/.ssh/id_ed25519" ]; then
  ssh_args=(--ssh "default=$HOME/.ssh/id_ed25519")
elif [ -r "$HOME/.ssh/id_rsa" ]; then
  ssh_args=(--ssh "default=$HOME/.ssh/id_rsa")
else
  echo "No SSH agent or readable SSH key found; Docker builds clone sources via SSH." >&2
  exit 2
fi

build_one() {
  local name="$1"
  local image="sqleek-${name}:${tag}"
  local dockerfile="$SCRIPT_DIR/${name}/Dockerfile"
  [ -f "$dockerfile" ] || { echo "missing Dockerfile: $dockerfile" >&2; exit 2; }
  echo "[build] $image from $dockerfile"
  DOCKER_BUILDKIT=1 docker build \
    "${ssh_args[@]}" \
    --build-arg "BASE_IMAGE=${base_image}" \
    --label org.sqleek.stage=stage3 \
    --label org.sqleek.dbms="$name" \
    --label org.sqleek.sqlright.commit=9457f0311b70562a3423ee86ac7e2ebdaaa6664b \
    --label org.sqleek.aflplusplus.commit=011cd189801830253c66ecd3cd6919ec01b46c34 \
    -t "$image" \
    -f "$dockerfile" .
  docker image inspect "$image" --format '{{.Id}} {{.Size}}' > "$SCRIPT_DIR/.last_${name}_image.txt"
}

if [ "$dbms" = "all" ]; then
  for one in mysql mariadb postgresql monetdb; do
    build_one "$one"
  done
else
  build_one "$dbms"
fi
