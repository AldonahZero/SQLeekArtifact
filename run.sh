#!/usr/bin/env bash
set -euo pipefail

# SQLeek entrypoint:
# Stage 0 (when its result is missing) -> Stage 1 (when its result is missing)
# -> Stage 2 LLM seeds -> Stage 3 Docker fuzzer.

ROOT="${SQLEEK_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PIPE="$ROOT/sqleek_pipeline"

DBMS="${SQLEEK_DBMS:-${1:-mysql}}"
RUN_ID="${SQLEEK_RUN_ID:-${2:-mysqldemo}}"
DURATION="${SQLEEK_DURATION:-${3:-60s}}"
FUZZER_DIR="${SQLEEK_FUZZER_DIR:-$PIPE/stage3_scheduler}"
TARGET_DIR="${SQLEEK_STAGE1_TARGET_DIR:-$PIPE/stage1_static/output/targets}"
OUTPUT_ROOT="${SQLEEK_RUN_OUTPUT_ROOT:-$FUZZER_DIR/output/runs}"

usage() {
  cat <<'EOF'
Usage:
  bash /root/SQLeek/run.sh [dbms] [run_id] [duration]

Defaults:
  dbms     mysql
  run_id   mysqldemo
  duration 60s

Environment overrides:
  SQLEEK_FUZZER_DIR       Fuzzer directory (default: sqleek_pipeline/stage3_scheduler)
  SQLEEK_IMAGE_TAG        Docker image tag
  SQLEEK_SKIP_FUZZER_BUILD=1  Reuse an existing image
  SQLEEK_RUN_OUTPUT_ROOT  Fuzzer output directory
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

case "$DBMS" in
  postgres) FUZZER_DBMS=postgresql ;;
  postgresql) STAGE_DBMS=postgres; FUZZER_DBMS=postgresql ;;
  mysql|mariadb|monetdb)
    STAGE_DBMS="$DBMS"
    FUZZER_DBMS="$DBMS"
    ;;
  *)
    echo "Unsupported DBMS: $DBMS" >&2
    usage >&2
    exit 2
    ;;
esac
STAGE_DBMS="${STAGE_DBMS:-$DBMS}"

LOG_DIR="$PIPE/output"
mkdir -p "$LOG_DIR" "$OUTPUT_ROOT"
exec > >(tee -a "$LOG_DIR/run.log") 2>&1

log() {
  printf '[sqleek-run] %s\n' "$*"
}

stage0_score="$PIPE/stage0_pre_processing/output/${STAGE_DBMS}_priority_scores.json"
if [[ -s "$stage0_score" ]]; then
  log "Stage 0 result exists; skip: $stage0_score"
else
  log "Stage 0 result missing; running ${STAGE_DBMS}"
  python3 "$PIPE/stage0_pre_processing/preprocess.py" --dbms "$STAGE_DBMS"
fi

stage1_result_dir="$PIPE/stage1_static/output/codeql_results/$STAGE_DBMS"
stage1_target_dir="$PIPE/stage1_static/output/targets"
stage1_required=(
  "$stage1_result_dir/dbms_memory_sinks.csv"
  "$stage1_result_dir/dbms_callchain.csv"
  "$stage1_target_dir/${STAGE_DBMS}_memory.txt"
  "$stage1_target_dir/${STAGE_DBMS}_stale.txt"
  "$stage1_target_dir/callchains.json"
  "$stage1_target_dir/phi_mapping.json"
)

stage1_ready=1
for artifact in "${stage1_required[@]}"; do
  if [[ ! -e "$artifact" ]]; then
    stage1_ready=0
    break
  fi
done

if [[ "$stage1_ready" -eq 1 ]]; then
  log "Stage 1 results exist; skip: $stage1_result_dir and $stage1_target_dir"
else
  log "Stage 1 results missing; running ${STAGE_DBMS}"
  SQLEEK_DBMS="$STAGE_DBMS" bash "$PIPE/stage1_static/build_and_run.sh" --dbms "$STAGE_DBMS"
fi

log "Stage 2: generate LLM SQL seeds"
python3 "$PIPE/stage2_setup/gen_seeds.py"

SEED_DIR="${SQLEEK_SEED_DIR:-$PIPE/stage2_setup/output/seeds/$STAGE_DBMS/memory}"
if ! find "$SEED_DIR" -maxdepth 1 -type f -name '*.sql' -size +0c -print -quit | grep -q .; then
  echo "No non-empty SQL seeds found: $SEED_DIR" >&2
  exit 1
fi

FUZZER_BUILD="$FUZZER_DIR/docker/build.sh"
FUZZER_RUN="$FUZZER_DIR/docker/run.sh"
[[ -f "$FUZZER_BUILD" ]] || { echo "Missing fuzzer build script: $FUZZER_BUILD" >&2; exit 1; }
[[ -f "$FUZZER_RUN" ]] || { echo "Missing fuzzer run script: $FUZZER_RUN" >&2; exit 1; }

IMAGE_TAG="${SQLEEK_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d)}"
if [[ "${SQLEEK_SKIP_FUZZER_BUILD:-0}" == "1" ]]; then
  log "Skip fuzzer build (SQLEEK_SKIP_FUZZER_BUILD=1)"
else
  log "Build fuzzer in $FUZZER_DIR: ${FUZZER_DBMS}, image tag ${IMAGE_TAG}"
  (
    cd "$FUZZER_DIR"
    SQLEEK_IMAGE_TAG="$IMAGE_TAG" bash "$FUZZER_BUILD" "$FUZZER_DBMS"
  )
fi

log "Run fuzzer: dbms=${FUZZER_DBMS} run_id=${RUN_ID} duration=${DURATION}"
SQLEEK_IMAGE_TAG="$IMAGE_TAG" bash "$FUZZER_RUN" \
  "$FUZZER_DBMS" "$RUN_ID" "$DURATION" \
  "$SEED_DIR" "$TARGET_DIR" "$OUTPUT_ROOT"

log "Done. Output: $OUTPUT_ROOT/$FUZZER_DBMS/$RUN_ID"
