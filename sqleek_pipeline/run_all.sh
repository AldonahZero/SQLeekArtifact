#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek
PIPE="$ROOT/sqleek_pipeline"
LOG_DIR="$PIPE/output"
LOG="$LOG_DIR/pipeline.log"

mkdir -p "$LOG_DIR"
touch "$LOG"

log() {
  local line
  line="$(date -Is) $*"
  printf '%s\n' "$line" | tee -a "$LOG"
}

run_stage() {
  local name="$1"
  local cmd="$2"
  log "=== [$name] start ==="
  bash -lc "$cmd" 2>&1 | tee -a "$LOG"
  local rc="${PIPESTATUS[0]}"
  if [[ "$rc" -ne 0 ]]; then
    log "=== [$name] failed rc=$rc ==="
    return "$rc"
  fi
  log "=== [$name] done ==="
}

usage() {
  cat <<'EOF'
Usage:
  run_all.sh [--skip-stage1] [--skip-stage2] [--duration SECONDS] [--dbms postgres|sqlite|mysql]

Behavior:
  - Default: run Stage 1 only.
  - If --duration > 0: run verify + (fuzz + online scheduler) for the selected DBMS.
  - --skip-stage1: do not run Stage 1.
  - --skip-stage2: do not run Stage 2 seed generation (assumes seeds already exist under stage2_setup/output/seeds/).

Examples:
  # Full (Stage 1 only)
  bash sqleek_pipeline/run_all.sh

  # Skip Stage 1/2, only verify + fuzz + scheduler for 1 hour
  bash sqleek_pipeline/run_all.sh --skip-stage1 --skip-stage2 --duration 3600 --dbms postgres
EOF
}

SKIP_STAGE1=0
SKIP_STAGE2=0
DBMS="postgres"
# 0 means "run until manually stopped"
DURATION="${FUZZ_DURATION:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-stage1) SKIP_STAGE1=1; shift ;;
    --skip-stage2) SKIP_STAGE2=1; shift ;;
    --dbms) DBMS="${2:-}"; shift 2 ;;
    --duration) DURATION="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

case "$DBMS" in
  postgres|sqlite|mysql) ;;
  *) echo "Invalid --dbms: $DBMS" >&2; exit 2 ;;
esac

if [[ "$SKIP_STAGE1" -eq 0 ]]; then
  run_stage "stage1_static" "$PIPE/stage1_static/build_and_run.sh"
fi

# Stage 2/3: online fuzzing + scheduler (optional)
if [[ "${DURATION:-0}" -ge 0 && ( "$SKIP_STAGE1" -eq 1 || "$SKIP_STAGE2" -eq 1 || "$DURATION" -gt 0 ) ]]; then
  # Stage 2 seed generation is intentionally not run here unless user wires it in.
  # We only check seeds exist so run_fuzz can mount them into the fuzz container.
  if [[ "$SKIP_STAGE2" -eq 0 ]]; then
    log "NOTE: Stage 2 seed generation is not invoked by run_all.sh. Set --skip-stage2 to acknowledge seeds are pre-generated."
  fi

  SEED_DIR="$PIPE/stage2_setup/output/seeds/$DBMS/memory"
  if [[ ! -d "$SEED_DIR" ]]; then
    echo "Missing seeds dir: $SEED_DIR" >&2
    echo "Run Stage 2 seed generation first (gen_seeds.py), or place seeds under that directory." >&2
    exit 1
  fi

  run_stage "stage3_verify" "bash $PIPE/stage3_scheduler/verify_binaries.sh --dbms $DBMS"

  log "=== [stage3_online] start fuzz + scheduler ==="
  bash -lc "bash $PIPE/stage3_scheduler/run_fuzz.sh --dbms $DBMS $DURATION" 2>&1 | tee -a "$LOG" &
  FUZZ_PID=$!

  python3 "$PIPE/stage3_scheduler/seed_scheduler.py" \
    --mode online \
    --dbms "$DBMS" \
    --duration "$DURATION" 2>&1 | tee -a "$LOG" &
  SCHED_PID=$!

  if [[ "$DURATION" -gt 0 ]]; then
    wait "$FUZZ_PID" || true
    kill "$SCHED_PID" >/dev/null 2>&1 || true
  else
    log "No duration specified (duration=0): fuzz + scheduler will run until manually stopped."
    log "Stop fuzz container with: docker stop sqleek_fuzz_${DBMS}"
    log "Stop scheduler with: pkill -f 'seed_scheduler.py.*--mode online.*--dbms ${DBMS}'"
    wait "$FUZZ_PID" || true
  fi
  log "=== [stage3_online] done ==="
fi
