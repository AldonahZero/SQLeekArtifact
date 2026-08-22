#!/bin/bash
set -e

cd /root/SQLeek
STAGE_DIR="/root/SQLeek/sqleek_pipeline/stage3_scheduler"
OUTPUT_DIR="$STAGE_DIR/output"
mkdir -p "$OUTPUT_DIR"
exec > >(tee -a "$OUTPUT_DIR/build.log") 2>&1

RUNTIME_JSON="$OUTPUT_DIR/griffin_runtime.json"

log() {
  printf '[verify_binaries] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage:
  verify_binaries.sh [--dbms postgres|sqlite|mysql]

Notes:
  - Default (no args): verify sqlite+postgres+mysql
  - With --dbms: verify only the specified DBMS
EOF
}

DBMS_LIST=("sqlite" "postgres" "mysql")
if [[ $# -gt 0 ]]; then
  case "${1:-}" in
    --dbms)
      case "${2:-}" in
        sqlite|postgres|mysql) DBMS_LIST=("${2}"); shift 2 ;;
        *) echo "Invalid --dbms: ${2:-}" >&2; usage; exit 2 ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: ${1:-}" >&2; usage; exit 2 ;;
  esac
fi

container_name() {
  local dbms="$1"
  printf 'sqleek_griffin_%s' "$dbms"
}

image_name() {
  local dbms="$1"
  case "$dbms" in
    sqlite) printf 'griffin_sqlite' ;;
    postgres) printf 'griffin_postgres' ;;
    mysql) printf 'griffin_mysql' ;;
    *) return 1 ;;
  esac
}

ensure_container() {
  local dbms="$1"
  local image="$2"
  local container="$3"

  if docker ps -a --format '{{.Names}}' | awk -v name="$container" '$0 == name { found=1 } END { exit !found }'; then
    if ! docker ps --format '{{.Names}}' | awk -v name="$container" '$0 == name { found=1 } END { exit !found }'; then
      log "starting existing container $container"
      docker start "$container" >/dev/null
    fi
    return 0
  fi

  if ! docker image inspect "$image" >/dev/null 2>&1; then
    log "missing image $image for $dbms"
    return 1
  fi

  log "creating container $container from $image"
  docker run --privileged -itd \
    -m "${GRIFFIN_MEMORY:-16G}" \
    --cpus="${GRIFFIN_CPUS:-4}" \
    --shm-size="${GRIFFIN_SHM:-2G}" \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1 \
    -e SQUIRREL_DISABLE_EXTRACT_STRUCT=1 \
    -e SQUIRREL_DISABLE_VALIDATE=1 \
    -e SQUIRREL_BOTH_MERGE_AND_UNMERGE=1 \
    --name "$container" "$image" >/dev/null
}

first_existing_path() {
  local container="$1"
  shift
  local candidate
  for candidate in "$@"; do
    if docker exec "$container" bash -lc "test -e '$candidate'" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

write_runtime_json() {
  python3 - "$RUNTIME_JSON" "$@" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
entries = {}
for item in sys.argv[2:]:
    dbms, container, image, status, harness, mutator = item.split("|", 5)
    entries[dbms] = {
        "container": container,
        "image": image,
        "status": status,
        "harness": harness,
        "mutator": mutator,
        "start_script": "/workspace/scripts/start_all.sh",
        "queue_dir": "/workspace/fuzzing/fuzz_out_dir/default/queue",
        "crashes_dir": "/workspace/fuzzing/fuzz_out_dir/default/crashes",
        "log_saved_dir": "/workspace/fuzzing/logSaved",
    }
path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if ! command -v docker >/dev/null 2>&1; then
  log "docker unavailable; cannot verify Griffin images"
  exit 1
fi

entries=()
critical_missing=0

printf '%-10s %-24s %-18s %-8s %-54s %s\n' "DBMS" "CONTAINER" "IMAGE" "STATUS" "HARNESS" "MUTATOR"
for dbms in "${DBMS_LIST[@]}"; do
  image="$(image_name "$dbms")"
  container="$(container_name "$dbms")"
  status="ok"
  harness=""
  mutator=""

  if ensure_container "$dbms" "$image" "$container"; then
    harness="$(first_existing_path "$container" \
      /workspace/bld_griffin_dynamic/autodriver_odbc_v5_aflpp \
      /workspace/bld_griffin/autodriver_odbc_v5_aflpp \
      /workspace/scripts/testt || true)"
    mutator="$(first_existing_path "$container" \
      "/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_${dbms}.so" \
      /workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so \
      "/workspace/bld_griffin/custom_mutator/squirrel_dependencies/libsquirrel_${dbms}.so" \
      /workspace/bld_griffin/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so \
      /workspace/bld_griffin_dynamic/custom_mutator/libmerge_odbc_ver_dynamic.so || true)"

    if [ -z "$harness" ] || [ -z "$mutator" ]; then
      status="missing-critical"
      critical_missing=1
    fi
  else
    status="missing-image"
    critical_missing=1
  fi

  printf '%-10s %-24s %-18s %-8s %-54s %s\n' "$dbms" "$container" "$image" "$status" "${harness:-NA}" "${mutator:-NA}"
  entries+=("$dbms|$container|$image|$status|$harness|$mutator")
done

write_runtime_json "${entries[@]}"
log "wrote $RUNTIME_JSON"

if [ "$critical_missing" -ne 0 ]; then
  log "critical Griffin binaries missing"
  exit 1
fi

log "Griffin binary verification complete"

