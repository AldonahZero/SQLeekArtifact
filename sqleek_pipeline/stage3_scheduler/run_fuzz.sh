#!/bin/bash
# run_fuzz.sh — SQLeek Stage3 fuzzing launcher
# Fixes:
#   1. Patch Griffin reset scripts: replace infinite `while killall` loop with
#      a bounded loop that skips zombie processes.
#   2. Patch Griffin reset scripts: remove stale PostgreSQL lock files before
#      initdb, so postgres can start even when zombie pg_c_<port> processes
#      left /tmp/.s.PGSQL.<port>.lock behind.

set -e

cd /root/SQLeek
STAGE_DIR="/root/SQLeek/sqleek_pipeline/stage3_scheduler"
OUTPUT_DIR="$STAGE_DIR/output"
mkdir -p "$OUTPUT_DIR"
exec > >(tee -a "$OUTPUT_DIR/build.log") 2>&1

DBMS_ONLY=""
# 0 means "run until manually stopped"
FUZZ_DURATION="0"

usage() {
  cat <<'EOF'
Usage:
  run_fuzz.sh [--dbms postgres|sqlite|mysql] [duration_seconds]

Notes:
  - If --dbms is set: only run that DBMS.
  - duration_seconds defaults to 0 (run until manually stopped).
  - By default the fuzz container is preserved (no --rm). Set KEEP_CONTAINER=0 to auto-remove.
EOF
}

if [[ $# -gt 0 ]]; then
  case "${1:-}" in
    --dbms)
      case "${2:-}" in
        sqlite|postgres|mysql) DBMS_ONLY="${2}"; shift 2 ;;
        *) echo "Invalid --dbms: ${2:-}" >&2; usage; exit 2 ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
  esac
fi

if [[ $# -gt 0 ]]; then
  FUZZ_DURATION="${1:-0}"
fi

RUNTIME_JSON="$OUTPUT_DIR/griffin_runtime.json"
FUZZ_OUTPUT_DIR="$OUTPUT_DIR/fuzz"

log() {
  printf '[run_fuzz] %s\n' "$*"
}

json_value() {
  local dbms="$1"
  local key="$2"
  python3 - "$RUNTIME_JSON" "$dbms" "$key" <<'PY'
import json
import sys
from pathlib import Path

path, dbms, key = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if not path.exists():
    print("")
    raise SystemExit
data = json.loads(path.read_text(encoding="utf-8"))
print(data.get(dbms, {}).get(key, ""))
PY
}

seed_dir_for_dbms() {
  local dbms="$1"
  printf '%s\n' "/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds/${dbms}/memory"
}

prefilter_seeds() {
  local dbms="$1"
  local seed_dir="$2"
  local keep_dir="$3"
  local deferred_dir="$4"

  rm -rf "$keep_dir" "$deferred_dir"
  mkdir -p "$keep_dir" "$deferred_dir"
  log "prefiltering seeds for $dbms: $seed_dir -> keep=$keep_dir deferred=$deferred_dir"

  local extra_args=()
  if [[ -n "${SQLEEK_LOW_THRESHOLD:-}" ]]; then
    extra_args+=(--low-threshold "$SQLEEK_LOW_THRESHOLD")
  fi
  if [[ -n "${SQLEEK_HIGH_THRESHOLD:-}" ]]; then
    extra_args+=(--high-threshold "$SQLEEK_HIGH_THRESHOLD")
  fi
  if [[ -n "${SQLEEK_STATE_BONUS_WEIGHT:-}" ]]; then
    extra_args+=(--state-bonus-weight "$SQLEEK_STATE_BONUS_WEIGHT")
  fi
  python3 "/root/SQLeek/sqleek_pipeline/stage3_scheduler/seed_scheduler.py" \
    --mode prefilter \
    --dbms "$dbms" \
    --seed-dir "$seed_dir" \
    --out-keep-dir "$keep_dir" \
    --out-deferred-dir "$deferred_dir" \
    "${extra_args[@]}"
}

if [ ! -f "$RUNTIME_JSON" ]; then
  log "runtime config missing; run sqleek_pipeline/stage3_scheduler/verify_binaries.sh first"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  log "docker unavailable"
  exit 1
fi

DBMS_LIST=("sqlite" "postgres" "mysql")
if [[ -n "$DBMS_ONLY" ]]; then
  DBMS_LIST=("$DBMS_ONLY")
fi

# ---------------------------------------------------------------------------
# Write the patch script to host disk, then bind-mount it into the container.
# This avoids heredoc-inside-heredoc quoting nightmares entirely.
#
# Two fixes applied to each reset_lv*.sh:
#   Fix 1 — replace infinite `while killall` loop with a bounded loop that
#            ignores zombie processes (zombies match killall but can't be killed).
#   Fix 2 — remove stale PostgreSQL socket/lock files before initdb, so a new
#            postgres can start even when zombie processes left them behind.
# ---------------------------------------------------------------------------
PATCH_SCRIPT_HOST="$OUTPUT_DIR/patch_reset.py"
cat > "$PATCH_SCRIPT_HOST" << 'PYEOF'
#!/usr/bin/env python3
"""
Patch Griffin reset_lv*.sh:

Fix 1 — Replace infinite `while killall -9 "$exe_name"` loop with a
         bounded loop that skips zombie processes.

Fix 2 — Insert `rm -f /tmp/.s.PGSQL.*.lock /tmp/.s.PGSQL.*` immediately
         before the `initdb` call, so stale lock files left by zombie
         processes don't cause postgres to FATAL on startup.
"""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
original = text

# ── Fix 1: replace the infinite killall loop ─────────────────────────────────

new_kill_block = r"""for _sqleek_i in $(seq 1 20)
do
    # Skip zombies — they cannot be killed and must not block reset.
    pkill -9 -x "$exe_name" 2>/dev/null || true
    if ps -eo stat=,comm= | awk -v n="$exe_name" '$2==n && $1 !~ /^Z/ {found=1} END{exit(found?0:1)}'; then
        echo "waiting killing done (attempt $_sqleek_i)..."
        sleep 1
    else
        break
    fi
done
"""

fix1_done = False
for pat in [
    'while killall -9 "$exe_name"\ndo\n    echo "waiting killing done..."\n    sleep 1\ndone\n',
    'while killall -9 "$exe_name"\ndo\n\techo "waiting killing done..."\n\tsleep 1\ndone\n',
    'while killall -9 "$exe_name"\ndo\n  echo "waiting killing done..."\n  sleep 1\ndone\n',
]:
    if pat in text:
        text = text.replace(pat, new_kill_block)
        fix1_done = True
        print(f"[patch fix1] exact match replaced in {path}")
        break

if not fix1_done:
    regex = (
        r'while\s+killall\s+-9\s+"\$exe_name"\s*\n'
        r'\s*do\s*\n'
        r'\s*echo\s+"waiting killing done\.\.\."\s*\n'
        r'\s*sleep\s+1\s*\n'
        r'\s*done\s*\n'
    )
    text, n = re.subn(regex, new_kill_block, text, flags=re.MULTILINE)
    if n > 0:
        fix1_done = True
        print(f"[patch fix1] regex match replaced ({n} occurrence(s)) in {path}")

if not fix1_done:
    print(f"[patch fix1] WARNING: killall pattern not found in {path} — skipped", file=sys.stderr)
    for i, line in enumerate(text.splitlines(), 1):
        print(f"  {i:3d}: {repr(line)}", file=sys.stderr)

# ── Fix 2: remove stale lock files before initdb ─────────────────────────────

text, n2 = re.subn(
    r'(sudo\s+-E\s+-u\s+postgres\s+\S*initdb\b)',
    r'rm -f /tmp/.s.PGSQL.*.lock /tmp/.s.PGSQL.*\n\1',
    text,
)
if n2 > 0:
    print(f"[patch fix2] inserted lock-file cleanup before initdb ({n2} occurrence(s)) in {path}")
else:
    print(f"[patch fix2] WARNING: initdb line not found in {path} — lock cleanup not inserted", file=sys.stderr)

# ── Write only if something changed ──────────────────────────────────────────

if text != original:
    path.write_text(text, encoding="utf-8")
    print(f"[patch] wrote {path}")
else:
    print(f"[patch] WARNING: no changes made to {path}", file=sys.stderr)
PYEOF
chmod +x "$PATCH_SCRIPT_HOST"
log "patch script written to $PATCH_SCRIPT_HOST"

# ---------------------------------------------------------------------------

for dbms in "${DBMS_LIST[@]}"; do
  container="$(json_value "$dbms" container)"
  image="$(json_value "$dbms" image)"
  harness="$(json_value "$dbms" harness)"
  mutator="$(json_value "$dbms" mutator)"
  status="$(json_value "$dbms" status)"
  start_script="$(json_value "$dbms" start_script)"
  fuzz_container="sqleek_fuzz_${dbms}"
  seed_dir="$(seed_dir_for_dbms "$dbms")"
  host_out="$FUZZ_OUTPUT_DIR/${dbms}_memory"

  if [ -z "$container" ] || [ "$status" != "ok" ]; then
    log "$dbms skipped because Griffin verification status is '${status:-missing}'"
    continue
  fi
  if [ -z "$image" ] || [ -z "$mutator" ]; then
    log "$dbms skipped because runtime image/mutator is missing"
    continue
  fi
  if [ ! -d "$seed_dir" ]; then
    log "$dbms skipped because seed dir missing: $seed_dir"
    continue
  fi

  keep_dir="$OUTPUT_DIR/prefilter/${dbms}/memory/keep"
  deferred_dir="$OUTPUT_DIR/prefilter/${dbms}/memory/deferred"
  prefilter_seeds "$dbms" "$seed_dir" "$keep_dir" "$deferred_dir"
  seed_dir="$keep_dir"
  if [ ! -d "$seed_dir" ] || [ -z "$(ls -A "$seed_dir" 2>/dev/null || true)" ]; then
    log "$dbms prefilter produced empty keep dir; aborting fuzz start"
    exit 1
  fi

  mkdir -p "$host_out/default/queue" "$host_out/default/crashes" "$host_out/.deferred"

  SQLEEK_QUEUE_FILTER_SO="$STAGE_DIR/sqleek_mutator.so"
  if [ ! -f "$SQLEEK_QUEUE_FILTER_SO" ] || [ "${SQLEEK_REBUILD_QUEUE_FILTER:-0}" = "1" ]; then
    log "building SQLeek AFL++ queue filter: $SQLEEK_QUEUE_FILTER_SO"
    gcc -shared -fPIC -O2 -o "$SQLEEK_QUEUE_FILTER_SO" "$STAGE_DIR/sqleek_mutator.c"
  fi

  # Clean stale AFL output so AFL++ can initialise its output dir cleanly.
  log "clearing stale AFL output dir: $host_out/default"
  rm -rf "$host_out/default"
  mkdir -p "$host_out/default/queue" "$host_out/default/crashes" "$host_out/default/hangs"

  docker rm -f "$fuzz_container" >/dev/null 2>&1 || true

  log "starting online Griffin fuzzing for $dbms in $fuzz_container (duration=${FUZZ_DURATION}s)"
  log "bind-mounted fuzz output: $host_out -> /fuzz_output"

  rm_flag=""
  if [ "${KEEP_CONTAINER:-1}" = "0" ]; then
    rm_flag="--rm"
    log "KEEP_CONTAINER=0: fuzz container will be auto-removed after stop"
  else
    log "KEEP_CONTAINER=1(default): fuzz container will be preserved after stop"
  fi

  # -------------------------------------------------------------------------
  # Start the fuzz container.
  #
  # Important:
  #   • NO set -e inside the container startup script — a failed step must not
  #     kill the container before `exec sleep infinity`.
  #   • patch_reset.py is bind-mounted from the host; no heredoc quoting needed.
  #   • Stale AFL output is cleared on the host side (above) before docker run.
  # -------------------------------------------------------------------------
  docker run -itd $rm_flag --name "$fuzz_container" \
    --privileged \
    -m 70G \
    --cpus=10 \
    --shm-size=5G \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1 \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQUIRREL_DISABLE_EXTRACT_STRUCT=1 \
    -e SQUIRREL_DISABLE_VALIDATE=1 \
    -e SQUIRREL_BOTH_MERGE_AND_UNMERGE=1 \
    -v "$seed_dir:/sqleek_seeds:ro" \
    -v "$host_out:/fuzz_output" \
    -v "$SQLEEK_QUEUE_FILTER_SO:/workspace/sqleek_queue_filter.so:ro" \
    -v "$PATCH_SCRIPT_HOST:/sqleek_patch_reset.py:ro" \
    "$image" bash -lc '
      # NO set -e — every step is fault-tolerant so we always reach sleep infinity.

      echo "[container-init] starting"

      # 1. Inject SQLeek seeds on top of Griffin builtin seeds.
      if [ -d /sqleek_seeds ]; then
        mkdir -p /workspace/seeds
        echo "[container-init] copying SQLeek seeds -> /workspace/seeds"
        cp -a /sqleek_seeds/. /workspace/seeds/ || echo "[WARN] seed copy had errors (non-fatal)"
      fi

      # 2. Apply both patches to Griffin reset scripts.
      echo "[container-init] patching Griffin reset scripts"
      for reset_sh in /workspace/scripts/reset_lv1.sh /workspace/scripts/reset_lv2.sh; do
        if [ -f "$reset_sh" ]; then
          echo "[container-init] patching $reset_sh"
          python3 /sqleek_patch_reset.py "$reset_sh" \
            && echo "[container-init] patch OK: $reset_sh" \
            || echo "[WARN] patch FAILED for $reset_sh — check docker logs"
        else
          echo "[container-init] $reset_sh not found, skipping"
        fi
      done

      # 3. Show patch result for docker logs inspection.
      echo "[container-init] post-patch verification:"
      grep -n "killall\|pkill\|_sqleek_i\|s\.PGSQL\|initdb" \
        /workspace/scripts/reset_lv1.sh \
        /workspace/scripts/reset_lv2.sh 2>/dev/null || true

      # 4. Set up fuzz output directory (no symlink — AFL++ cannot clean through one).
      mkdir -p /fuzz_output/default/queue \
               /fuzz_output/default/crashes \
               /fuzz_output/default/hangs \
               /workspace/fuzzing
      rm -rf /workspace/fuzzing/fuzz_out_dir
      mkdir -p /workspace/fuzzing/fuzz_out_dir/default/queue \
               /workspace/fuzzing/fuzz_out_dir/default/crashes \
               /workspace/fuzzing/fuzz_out_dir/default/hangs

      echo "[container-init] done — entering sleep infinity"
      exec sleep infinity
    '

  # Confirm the container is still alive after init.
  sleep 3
  if ! docker ps --filter "name=^${fuzz_container}$" --filter "status=running" \
       --format '{{.Names}}' | grep -q "^${fuzz_container}$"; then
    log "ERROR: container $fuzz_container exited unexpectedly. Last logs:"
    docker logs "$fuzz_container" | tail -40
    exit 1
  fi
  log "$dbms container $fuzz_container is running"

  # Confirm both patches are visible inside the container.
  log "verifying patches inside $fuzz_container ..."
  docker exec "$fuzz_container" bash -lc '
    for f in /workspace/scripts/reset_lv1.sh /workspace/scripts/reset_lv2.sh; do
      echo "=== $f ==="
      grep -n "killall\|pkill\|_sqleek_i\|s\.PGSQL\|initdb" "$f" 2>/dev/null || echo "(not found)"
    done
  ' || true

  log "$dbms seeds available via: docker exec -it $fuzz_container ls -la /workspace/seeds | head"

  # -------------------------------------------------------------------------
  # Start Griffin's fuzzing script inside the running container (detached).
  # -------------------------------------------------------------------------
  log "starting Griffin start_all.sh inside $fuzz_container"
  docker exec -d "$fuzz_container" bash -lc "
    set -e
    export AFL_CUSTOM_MUTATOR_LIBRARY=\"/workspace/sqleek_queue_filter.so:${mutator}\"
    export SQLEEK_AFL_TIMEOUT_MS=\"\${SQLEEK_AFL_TIMEOUT_MS:-5000}\"
    sqleek_start_script='${start_script:-/workspace/scripts/start_all.sh}'

    # Patch the AFL -t timeout value in start_all.sh.
    if [ -f \"\$sqleek_start_script\" ]; then
      sqleek_patched_start='/tmp/sqleek_start_all.sh'
      python3 - \"\$sqleek_start_script\" \"\$sqleek_patched_start\" \"\$SQLEEK_AFL_TIMEOUT_MS\" <<'PY'
import os
import re
import stat
import sys
from pathlib import Path

src, dst, timeout_ms = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(src).read_text(encoding='utf-8')
text = re.sub(r'(?<!\S)-t\s+[0-9]+', f'-t {timeout_ms}', text)
Path(dst).write_text(text, encoding='utf-8')
mode = os.stat(src).st_mode
os.chmod(dst, mode | stat.S_IXUSR)
PY
      sqleek_start_script=\"\$sqleek_patched_start\"
    fi

    mkdir -p /workspace/fuzzing/logSaved
    if [ '${FUZZ_DURATION}' -gt 0 ]; then
      (timeout '${FUZZ_DURATION}' \"\$sqleek_start_script\" || true) \
        > /workspace/fuzzing/logSaved/sqleek_start_all.log 2>&1
    else
      (\"\$sqleek_start_script\" || true) \
        > /workspace/fuzzing/logSaved/sqleek_start_all.log 2>&1
    fi
  "

  if [ "$FUZZ_DURATION" -gt 0 ]; then
    sleep "$FUZZ_DURATION" || true
    log "stopping fuzz container $fuzz_container"
    docker stop "$fuzz_container" >/dev/null 2>&1 || true
    log "$dbms fuzzing complete; live outputs under $host_out"
  else
    log "$dbms fuzzing started (no duration limit). Container will keep running."
    log "  docker ps | grep $fuzz_container"
    log "Stop manually when ready:"
    log "  docker stop $fuzz_container"
  fi
done

log "Griffin fuzzing stage complete"