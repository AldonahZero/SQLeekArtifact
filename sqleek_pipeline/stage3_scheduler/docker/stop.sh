#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: ./docker/stop.sh <mysql|mariadb|postgresql|monetdb> <run_id>" >&2
}

[ $# -eq 2 ] || { usage; exit 2; }
dbms="$1"
run_id="$2"
case "$dbms" in
  postgres) dbms="postgresql" ;;
  mysql|mariadb|postgresql|monetdb) ;;
  *) usage; exit 2 ;;
esac

container="sqleek_stage3_${dbms}_${run_id}"
if ! docker ps -a --format '{{.Names}}' | grep -Fxq "$container"; then
  echo "container not found: $container" >&2
  exit 1
fi
stage="$(docker inspect "$container" --format '{{index .Config.Labels "org.sqleek.stage"}}')"
rid="$(docker inspect "$container" --format '{{index .Config.Labels "org.sqleek.run_id"}}')"
if [ "$stage" != "stage3" ] || [ "$rid" != "$run_id" ]; then
  echo "refusing to stop non-matching container: $container" >&2
  exit 3
fi
docker stop "$container"
