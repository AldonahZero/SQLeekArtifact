#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
COLLECTION=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502
OUT_ROOT=/root/SQLeek/experiment/RQ2/replay/output
DBMS_LIST=mysql,postgres,sqlite
CHECKPOINTS_MIN=1440
MAX_SEEDS=0
SEED_TIMEOUT=120
PRECHECK_ONLY=0
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-120G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-4G}
TOOL=SQUIRREL

usage() {
  cat <<'USAGE'
Usage: rq2_replay_squirrel_coverage.sh [options]

Options:
  --collection DIR       SQUIRREL collection dir with manifest.tsv
  --out-root DIR         Output root directory
  --dbms LIST            Comma-separated DBMS list, default mysql,postgres,sqlite
  --checkpoints-min LIST Comma-separated cumulative replay checkpoints, default 1440
  --max-seeds N          Limit seeds per checkpoint for smoke tests; 0 means all
  --seed-timeout SEC     Timeout for each SQL seed, default 120
  --precheck-only        Only build target_regions.csv and check coverage backends

Backend env overrides:
  RQ2_MYSQL_IMAGE, RQ2_MYSQL_BINARY
  RQ2_POSTGRES_IMAGE, RQ2_POSTGRES_BINARY
  RQ2_SQLITE_IMAGE, RQ2_SQLITE_BINARY
  RQ2_REPLAY_DOCKER_MEM, RQ2_REPLAY_DOCKER_SHM
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --collection) COLLECTION="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --dbms) DBMS_LIST="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --max-seeds) MAX_SEEDS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --precheck-only) PRECHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

TS=$(date -u +%Y%m%d_%H%M%S)
OUT="$OUT_ROOT/real_squirrel_coverage_$TS"
mkdir -p "$OUT" "$OUT/work" "$OUT/data"

IFS=',' read -r -a DBMS_ARR <<< "$DBMS_LIST"
IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MIN"

python3 "$SCRIPT_DIR/build_target_regions.py" --dbms "$DBMS_LIST" --out "$OUT/target_regions.csv"

PREFLIGHT="$OUT/preflight_status.tsv"
INDEX="$OUT/replay_index.tsv"
printf 'dbms\timage\tbinary\tstatus\tmessage\n' > "$PREFLIGHT"
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$INDEX"

contains_dbms() {
  local needle=$1
  for x in "${DBMS_ARR[@]}"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

upper() { echo "$1" | tr '[:lower:]' '[:upper:]'; }

backend_image() {
  local dbms=$1 var="RQ2_$(upper "$dbms")_IMAGE"
  case "$dbms" in
    mysql) echo "${!var:-griffin_mysql_llvmcov}" ;;
    postgres) echo "${!var:-griffin_postgres_llvmcov}" ;;
    sqlite) echo "${!var:-griffin_sqlite_llvmcov}" ;;
    *) echo "" ;;
  esac
}

backend_binary() {
  local dbms=$1 var="RQ2_$(upper "$dbms")_BINARY"
  case "$dbms" in
    mysql) echo "${!var:-/root/bin_aflpp/usr/local/mysql/bin/mysqld}" ;;
    postgres) echo "${!var:-/root/bin_aflpp/usr/local/pgsql/bin/postgres}" ;;
    sqlite) echo "${!var:-/root/bin_aflpp/usr/local/bin/sqlite3}" ;;
    *) echo "" ;;
  esac
}

backend_process() {
  case "$1" in
    mysql) echo my_8888 ;;
    postgres) echo pg_c_8888 ;;
    sqlite) echo "" ;;
  esac
}

backend_profdata_bin() {
  local dbms=$1 var="RQ2_$(upper "$dbms")_LLVM_PROFDATA_BIN"
  case "$dbms" in
    mysql) echo "${!var:-llvm-profdata-14}" ;;
    postgres|sqlite) echo "${!var:-llvm-profdata-12}" ;;
    *) echo "" ;;
  esac
}

backend_cov_bin() {
  local dbms=$1 var="RQ2_$(upper "$dbms")_LLVM_COV_BIN"
  case "$dbms" in
    mysql) echo "${!var:-llvm-cov-14}" ;;
    postgres|sqlite) echo "${!var:-llvm-cov-12}" ;;
    *) echo "" ;;
  esac
}

BACKEND_OK_DIR="$OUT/backend_ok"
mkdir -p "$BACKEND_OK_DIR"

preflight_backend() {
  local dbms=$1 image binary profdata cov msg
  image=$(backend_image "$dbms")
  binary=$(backend_binary "$dbms")
  profdata=$(backend_profdata_bin "$dbms")
  cov=$(backend_cov_bin "$dbms")
  if [[ -z "$image" || -z "$binary" ]]; then
    printf '%s\t%s\t%s\tmissing\tno backend configured\n' "$dbms" "$image" "$binary" >> "$PREFLIGHT"
    return 1
  fi
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    msg="missing docker image; build or set RQ2_$(upper "$dbms")_IMAGE"
    printf '%s\t%s\t%s\tmissing\t%s\n' "$dbms" "$image" "$binary" "$msg" >> "$PREFLIGHT"
    return 1
  fi
  if docker run --rm --entrypoint /bin/bash "$image" -lc "command -v '$profdata' >/dev/null && command -v '$cov' >/dev/null && test -x '$binary' && strings '$binary' | grep -q '__llvm_prf'" >/dev/null 2>&1; then
    printf '%s\t%s\t%s\tok\tLLVM source coverage backend ready (%s/%s)\n' "$dbms" "$image" "$binary" "$profdata" "$cov" >> "$PREFLIGHT"
    touch "$BACKEND_OK_DIR/$dbms"
    return 0
  fi
  msg="image exists but llvm tools/binary/profile symbols are missing; not valid for target-branch replay"
  printf '%s\t%s\t%s\tinvalid\t%s\n' "$dbms" "$image" "$binary" "$msg" >> "$PREFLIGHT"
  return 1
}

for dbms in "${DBMS_ARR[@]}"; do
  preflight_backend "$dbms" || true
done

if [[ "$PRECHECK_ONLY" -eq 1 ]]; then
  echo "$OUT"
  exit 0
fi

MANIFEST="$COLLECTION/manifest.tsv"
if [[ ! -f "$MANIFEST" ]]; then
  echo "missing manifest: $MANIFEST" >&2
  exit 3
fi

while IFS=$'\t' read -r container image _status started_at _collected_at _run_time _queue_total _queue_24h _crashes_total _crashes_24h _hangs_total _hangs_24h _raw_tar cutoff_tar; do
  [[ "$container" == container ]] && continue
  if [[ ! "$container" =~ ^rq2_squirrel_([^_]+)_r([0-9]+)_ ]]; then
    continue
  fi
  dbms="${BASH_REMATCH[1]}"
  repeat="${BASH_REMATCH[2]}"
  contains_dbms "$dbms" || continue
  run_id="${dbms}_squirrel_r${repeat}"
  bimage=$(backend_image "$dbms")
  binary=$(backend_binary "$dbms")
  profdata=$(backend_profdata_bin "$dbms")
  cov=$(backend_cov_bin "$dbms")
  if [[ ! -f "$BACKEND_OK_DIR/$dbms" ]]; then
    for cp in "${CHECKPOINT_ARR[@]}"; do
      printf '%s\t%s\t%s\t%s\t%s\t\t\tunsupported\t%s\t%s\t%s\t0\t%s\t%s\t%s\t\t%s\t\n' \
        "$run_id" "$TOOL" "$dbms" "$repeat" "$cp" "coverage backend missing; see preflight_status.tsv" "$bimage" "$binary" "$cutoff_tar" "$bimage" "$container" "$started_at" >> "$INDEX"
    done
    continue
  fi
  if [[ ! -f "$cutoff_tar" ]]; then
    for cp in "${CHECKPOINT_ARR[@]}"; do
      printf '%s\t%s\t%s\t%s\t%s\t\t\tfailed\tmissing cutoff tar\t%s\t%s\t0\t%s\t%s\t%s\t\t%s\t\n' \
        "$run_id" "$TOOL" "$dbms" "$repeat" "$cp" "$bimage" "$binary" "$cutoff_tar" "$bimage" "$container" "$started_at" >> "$INDEX"
    done
    continue
  fi
  run_work="$OUT/work/$run_id"
  queue_dir="$run_work/queue"
  mkdir -p "$run_work/extract"
  if [[ ! -d "$queue_dir" ]]; then
    tar -xzf "$cutoff_tar" -C "$run_work/extract" --wildcards 'queue/*'
    mv "$run_work/extract/queue" "$queue_dir"
  fi
  seed_total=$(find "$queue_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  for cp in "${CHECKPOINT_ARR[@]}"; do
    cp_ms=$((cp * 60 * 1000))
    cp_out="$run_work/t${cp}"
    mkdir -p "$cp_out"
    name="rq2_replay_${dbms}_r${repeat}_t${cp}_${TS}_${RANDOM}"
    prefix="/rq2_out/${run_id}_t${cp}"
    host_prefix="$cp_out/${run_id}_t${cp}"
    status=complete
    message=
    start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    if ! docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
      -e GRIFFIN_CONTAINER=1 \
      -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
      -e LLVM_PROFDATA_BIN="$profdata" \
      -e LLVM_COV_BIN="$cov" \
      -v "$SCRIPT_DIR":/rq2_scripts:ro \
      -v "$queue_dir":/rq2_queue:ro \
      -v "$cp_out":/rq2_out \
      --name "$name" --entrypoint /bin/bash "$bimage" \
      /rq2_scripts/container_replay_llvm.sh \
        --dbms "$dbms" --binary "$binary" --checkpoint-ms "$cp_ms" \
        --max-seeds "$MAX_SEEDS" --seed-timeout "$SEED_TIMEOUT" \
        --process-name "$(backend_process "$dbms")" --out-prefix "$prefix"; then
      status=failed
      message="docker replay failed; inspect $cp_out"
    fi
    end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cov_json=
    report_txt=
    if [[ "$status" == complete && -f "${host_prefix}.cov.json" ]]; then
      cov_json="${host_prefix}.cov.json"
      report_txt="${host_prefix}.report.txt"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t\t%s\t%s\n' \
      "$run_id" "$TOOL" "$dbms" "$repeat" "$cp" "$cov_json" "$report_txt" "$status" "$message" "$bimage" "$binary" "$seed_total" "$cutoff_tar" "$bimage" "$container" "$start_time" "$end_time" >> "$INDEX"
  done
done < "$MANIFEST"

SUMMARY_ARGS=(
  --target-regions "$OUT/target_regions.csv"
  --replay-index "$INDEX"
  --out "$OUT/data"
  --tool "$TOOL"
)
if [[ ",${DBMS_LIST// /}," == *",sqlite,"* ]]; then
  SQLITE_MAP="$OUT/source_map/sqlite3_llvmcov.c"
  mkdir -p "$(dirname "$SQLITE_MAP")"
  if [[ ! -s "$SQLITE_MAP" ]] && docker image inspect griffin_sqlite_llvmcov >/dev/null 2>&1; then
    if docker run --rm griffin_sqlite_llvmcov cat /root/bld_llvmcov/sqlite3.c > "${SQLITE_MAP}.tmp"; then
      mv "${SQLITE_MAP}.tmp" "$SQLITE_MAP"
    else
      rm -f "${SQLITE_MAP}.tmp"
    fi
  fi
  if [[ -s "$SQLITE_MAP" ]]; then
    SUMMARY_ARGS+=(--sqlite-amalgamation "$SQLITE_MAP")
  fi
fi

python3 "$SCRIPT_DIR/summarize_llvm_cov.py" "${SUMMARY_ARGS[@]}"
echo "$OUT"
