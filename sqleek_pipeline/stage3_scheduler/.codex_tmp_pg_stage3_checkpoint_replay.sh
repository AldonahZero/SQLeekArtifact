#!/usr/bin/env bash
set -euo pipefail

BASE=/root/SQLeek/sqleek_pipeline/stage3_scheduler
RQ2_REPLAY=/root/SQLeek/experiment/RQ2/replay

SRC_CONTAINER=sqleek_stage3_postgresql_pg2x24h_staged500_copyfix0007_20260716_021147_r1
SRC_RUN=/root/sqleek_stage3_runs/postgresql/pg2x24h_staged500_copyfix0007_20260716_021147_r1
SRC_QUEUE=$SRC_RUN/output/postgres_memory/default/queue

# User requested this exact output location, even though the source DBMS is PostgreSQL.
DEST=$BASE/checkpoints/mysql/r1/queue
QUEUE_DST=$DEST/queue_files
REPLAY_QUEUE=$DEST/replay_queue_time_named
WORK=$DEST/work
LOG_DIR=$DEST/logs
REPLAY_DIR=$DEST/replay_out/pilot_current
DATA_DIR=$DEST/data
SCRIPT_ARCHIVE=$DEST/replay_scripts

CHECKPOINT_MIN=${CHECKPOINT_MIN:-60}
CHECKPOINT_MS=$((CHECKPOINT_MIN * 60 * 1000))
IMAGE=${RQ2_POSTGRES_IMAGE:-griffin_postgres_llvmcov}
BINARY=${RQ2_POSTGRES_BINARY:-/root/bin_llvmcov/usr/local/pgsql/bin/postgres}
RESET_SCRIPT=${RQ2_POSTGRES_RESET_SCRIPT:-/workspace/scripts/reset_lv1.sh}
TEST_SCRIPT=${RQ2_POSTGRES_TEST_SCRIPT:-/workspace/scripts/testt}
SEED_TIMEOUT=${RQ2_SEED_TIMEOUT:-60}
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-120G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-4G}
RUN_ID=postgres_sqleek_stage3_r1_pilot
TOOL=SQLeek
DBMS=postgres
REPEAT=1

if [[ ! -d "$SRC_QUEUE" ]]; then
  echo "missing source queue: $SRC_QUEUE" >&2
  exit 2
fi

if [[ -e "$DEST" ]]; then
  echo "destination already exists; refusing to overwrite: $DEST" >&2
  exit 3
fi

mkdir -p "$QUEUE_DST" "$REPLAY_QUEUE" "$WORK" "$LOG_DIR" "$REPLAY_DIR" "$DATA_DIR" "$SCRIPT_ARCHIVE"
cp -a "$0" "$SCRIPT_ARCHIVE/$(basename "$0")" 2>/dev/null || true

RUN_START_ISO=$(docker inspect "$SRC_CONTAINER" --format '{{.State.StartedAt}}')
RUN_STATUS=$(docker inspect "$SRC_CONTAINER" --format '{{.State.Status}}')
RUN_HEALTH=$(docker inspect "$SRC_CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}')

find "$SRC_QUEUE" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort > "$WORK/source_files.txt"
rsync -a --files-from="$WORK/source_files.txt" "$SRC_QUEUE/" "$QUEUE_DST/"

python3 - "$SRC_QUEUE" "$QUEUE_DST" "$REPLAY_QUEUE" "$DEST" "$RUN_START_ISO" "$SRC_CONTAINER" "$SRC_RUN" "$CHECKPOINT_MIN" <<'PY'
from __future__ import annotations
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

src = Path(sys.argv[1])
queue = Path(sys.argv[2])
replayq = Path(sys.argv[3])
dest = Path(sys.argv[4])
run_start_iso = sys.argv[5]
container = sys.argv[6]
run_root = sys.argv[7]
checkpoint_min = int(sys.argv[8])

def parse_docker_iso(s: str) -> datetime:
    s = s.strip()
    m = re.match(r"^(.*\.)(\d{6})\d*(Z|[+-]\d\d:\d\d)$", s)
    if m:
        s = m.group(1) + m.group(2) + m.group(3)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

run_start = parse_docker_iso(run_start_iso).timestamp()
manifest_rows = []
replay_rows = []
mtime_mismatch = 0
missing_source_after_copy = 0

files = [p for p in queue.iterdir() if p.is_file() and not p.is_symlink()]
files.sort(key=lambda p: p.name)
for p in files:
    source_path = src / p.name
    st = p.stat()
    if source_path.exists():
        sst = source_path.stat()
        if abs(sst.st_mtime - st.st_mtime) > 1e-6:
            mtime_mismatch += 1
    else:
        missing_source_after_copy += 1
    data = p.read_bytes()
    m = re.match(r"id:(\d+)", p.name)
    id_num = int(m.group(1)) if m else None
    is_initial = (",orig:" in p.name) or (id_num is not None and id_num < 500 and ",src:" not in p.name)
    is_hi = "hi_val_online_score" in p.name
    tm = re.search(r"(?:^|,)time:(\d+)(?:,|$)", p.name)
    if tm:
        elapsed_ms = int(tm.group(1))
        elapsed_source = "filename_time"
    elif is_initial:
        elapsed_ms = 0
        elapsed_source = "initial_orig"
    else:
        elapsed_ms = max(0, int(round((st.st_mtime - run_start) * 1000)))
        elapsed_source = "mtime_minus_container_start"
    row = {
        "source_path": str(source_path),
        "checkpoint_path": str(p),
        "filename": p.name,
        "size": st.st_size,
        "mtime_epoch": st.st_mtime,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "is_initial_corpus": bool(is_initial),
        "is_hi_val_online_score_input": bool(is_hi),
        "elapsed_ms": elapsed_ms,
        "elapsed_source": elapsed_source,
    }
    manifest_rows.append(row)

for seq, row in enumerate(sorted(manifest_rows, key=lambda r: (int(r["elapsed_ms"]), str(r["filename"]))), start=1):
    src_file = Path(row["checkpoint_path"])
    replay_name = f"time:{row['elapsed_ms']},seq:{seq:06d},{row['filename']}"
    dst_file = replayq / replay_name
    shutil.copy2(src_file, dst_file)
    replay_rows.append({
        "seed_time_ms": row["elapsed_ms"],
        "seq": seq,
        "original_name": row["filename"],
        "replay_name": replay_name,
        "checkpoint_path": row["checkpoint_path"],
        "replay_path": str(dst_file),
        "mtime_epoch": row["mtime_epoch"],
        "size": row["size"],
        "selected_for_checkpoint": int(row["elapsed_ms"]) <= checkpoint_min * 60 * 1000,
    })

with (dest / "manifest.jsonl").open("w", encoding="utf-8") as fp:
    for row in manifest_rows:
        fp.write(json.dumps(row, sort_keys=True) + "\n")

manifest_fields = [
    "source_path", "checkpoint_path", "filename", "size", "mtime_epoch", "mtime_utc",
    "sha256", "is_initial_corpus", "is_hi_val_online_score_input", "elapsed_ms", "elapsed_source",
]
with (dest / "manifest.tsv").open("w", newline="", encoding="utf-8") as fp:
    writer = csv.DictWriter(fp, fieldnames=manifest_fields, delimiter="\t")
    writer.writeheader()
    writer.writerows(manifest_rows)

replay_fields = [
    "seed_time_ms", "seq", "original_name", "replay_name", "checkpoint_path",
    "replay_path", "mtime_epoch", "size", "selected_for_checkpoint",
]
with (dest / "replay_index.tsv").open("w", newline="", encoding="utf-8") as fp:
    writer = csv.DictWriter(fp, fieldnames=replay_fields, delimiter="\t")
    writer.writeheader()
    writer.writerows(replay_rows)

summary = {
    "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "source_dbms": "postgres",
    "source_container": container,
    "source_run_root": run_root,
    "source_queue": str(src),
    "checkpoint_path": str(dest),
    "queue_files_path": str(queue),
    "replay_queue_path": str(replayq),
    "container_start_utc": parse_docker_iso(run_start_iso).isoformat().replace("+00:00", "Z"),
    "checkpoint_min": checkpoint_min,
    "queue_total": len(manifest_rows),
    "initial_corpus_count": sum(1 for r in manifest_rows if r["is_initial_corpus"]),
    "new_queue_count": sum(1 for r in manifest_rows if not r["is_initial_corpus"]),
    "hi_val_count": sum(1 for r in manifest_rows if r["is_hi_val_online_score_input"]),
    "total_size_bytes": sum(int(r["size"]) for r in manifest_rows),
    "replay_selected_count": sum(1 for r in replay_rows if r["selected_for_checkpoint"]),
    "mtime_mismatch_count": mtime_mismatch,
    "missing_source_after_copy_count": missing_source_after_copy,
    "earliest_mtime_utc": min((str(r["mtime_utc"]) for r in manifest_rows), default=""),
    "latest_mtime_utc": max((str(r["mtime_utc"]) for r in manifest_rows), default=""),
    "max_elapsed_ms": max((int(r["elapsed_ms"]) for r in manifest_rows), default=0),
}
(dest / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps(summary, sort_keys=True))
PY

python3 "$RQ2_REPLAY/build_target_regions.py" --dbms postgres --out "$DEST/target_regions.csv" > "$LOG_DIR/build_target_regions.log" 2>&1

RUNNER=$SCRIPT_ARCHIVE/postgres_bucketed_replay_with_status.sh
cat > "$RUNNER" <<'RUNNER_SH'
#!/usr/bin/env bash
set -euo pipefail

DBMS=
BINARY=
CHECKPOINTS_MS=
SEED_TIMEOUT=60
OUT_PREFIX=/rq2_out/replay
RESET_SCRIPT=/workspace/scripts/reset_lv1.sh
TEST_SCRIPT=/workspace/scripts/testt
PROCESS_NAME=pg_c_8888
SERVER_CHECK_INTERVAL=${RQ2_REPLAY_SERVER_CHECK_INTERVAL:-1}
LLVM_PROFDATA_BIN=${LLVM_PROFDATA_BIN:-llvm-profdata-12}
LLVM_COV_BIN=${LLVM_COV_BIN:-llvm-cov-12}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbms) DBMS="$2"; shift 2 ;;
    --binary) BINARY="$2"; shift 2 ;;
    --checkpoints-ms) CHECKPOINTS_MS="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --out-prefix) OUT_PREFIX="$2"; shift 2 ;;
    --reset-script) RESET_SCRIPT="$2"; shift 2 ;;
    --test-script) TEST_SCRIPT="$2"; shift 2 ;;
    --process-name) PROCESS_NAME="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$DBMS" || -z "$BINARY" || -z "$CHECKPOINTS_MS" ]]; then
  echo "--dbms, --binary and --checkpoints-ms are required" >&2
  exit 2
fi

PROFILE_DIR=/tmp/rq2_prof
mkdir -p "$(dirname "$OUT_PREFIX")" "$PROFILE_DIR" /workspace/logs /workspace/fuzzing
chmod 0777 "$PROFILE_DIR"
rm -rf "$PROFILE_DIR"/*
export LLVM_PROFILE_FILE="${LLVM_PROFILE_FILE:-%c/tmp/rq2_prof/%p-%m.profraw}"

if ! [[ "$SERVER_CHECK_INTERVAL" =~ ^[0-9]+$ ]] || [[ "$SERVER_CHECK_INTERVAL" -lt 1 ]]; then
  SERVER_CHECK_INTERVAL=1
fi

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MS"

python3 - "$CHECKPOINTS_MS" > /tmp/rq2_seed_buckets.log <<'PY'
from __future__ import annotations
import re
import sys
from pathlib import Path

checkpoints = sorted({int(x) for x in sys.argv[1].split(",") if x})
max_checkpoint = max(checkpoints)
rows = []
bad_time = []
for p in Path("/rq2_queue").glob("*"):
    if not p.is_file():
        continue
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", p.name)
    if not m:
        bad_time.append(p.name)
        continue
    t = int(m.group(1))
    if t <= max_checkpoint:
        rows.append((t, p.name, str(p)))
rows.sort(key=lambda x: (x[0], x[1]))
if bad_time:
    print(f"skipped_bad_time_seeds={len(bad_time)}", file=sys.stderr)
prev = -1
for cp in checkpoints:
    bucket = [row for row in rows if prev < row[0] <= cp]
    cumulative = [row for row in rows if row[0] <= cp]
    cp_min = cp // 60000
    with open(f"/tmp/rq2_bucket_{cp_min}.tsv", "w", encoding="utf-8") as fp:
        for t, name, path in bucket:
            fp.write(f"{t}\t{name}\t{path}\n")
    with open(f"/tmp/rq2_cumulative_{cp_min}.tsv", "w", encoding="utf-8") as fp:
        for t, name, path in cumulative:
            fp.write(f"{t}\t{name}\t{path}\n")
    print(f"checkpoint_min={cp_min}\tbucket={len(bucket)}\tcumulative={len(cumulative)}")
    prev = cp
PY

terminate_server() {
  if [[ -n "$PROCESS_NAME" ]]; then
    pkill -TERM "$PROCESS_NAME" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      pgrep "$PROCESS_NAME" >/dev/null 2>&1 || break
      sleep 0.5
    done
    pkill -INT "$PROCESS_NAME" >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL "$PROCESS_NAME" >/dev/null 2>&1 || true
  fi
}

server_alive() {
  local candidates=()
  [[ -n "$PROCESS_NAME" ]] && candidates+=("$PROCESS_NAME")
  candidates+=("pg_c_8888" "postgres")
  local name
  for name in "${candidates[@]}"; do
    [[ -n "$name" ]] || continue
    if pgrep -x "$name" >/dev/null 2>&1 || pgrep "$name" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

wait_for_server() {
  for _ in $(seq 1 20); do
    server_alive && return 0
    sleep 0.5
  done
  return 1
}

reset_server() {
  local reason="$1"
  local save_dir=""
  if [[ "$reason" != "initial" ]]; then
    save_dir=$(mktemp -d /tmp/rq2_prof_keep.XXXXXX)
    shopt -s nullglob
    local existing_profiles=("$PROFILE_DIR"/*.profraw "$PROFILE_DIR"/*.profdata)
    if [[ ${#existing_profiles[@]} -gt 0 ]]; then
      cp -a "${existing_profiles[@]}" "$save_dir"/
    fi
    shopt -u nullglob
  fi

  set +e
  "$RESET_SCRIPT" 8888 "$BINARY" >> "${OUT_PREFIX}.reset.log" 2>&1
  local rc=$?
  set -e

  if [[ -n "$save_dir" ]]; then
    mkdir -p "$PROFILE_DIR"
    chmod 0777 "$PROFILE_DIR"
    shopt -s nullglob
    local saved_profiles=("$save_dir"/*)
    if [[ ${#saved_profiles[@]} -gt 0 ]]; then
      cp -an "${saved_profiles[@]}" "$PROFILE_DIR"/ 2>/dev/null || true
    fi
    shopt -u nullglob
    rm -rf "$save_dir"
  fi
  return "$rc"
}

DBMS_SEED_INDEX=0
RESTART_LOG="${OUT_PREFIX}.server_restarts.tsv"
SEED_STATUS="${OUT_PREFIX}.seed_status.tsv"
printf 'timestamp_utc\tdbms\tcheckpoint_min\tseed_time\tseed_name\tseed_path\texit_code\taction\n' > "$RESTART_LOG"
printf 'timestamp_utc\tcheckpoint_min\tseed_time\tseed_name\tseed_path\texit_code\tstatus\n' > "$SEED_STATUS"
: > "${OUT_PREFIX}.reset.log"
: > "${OUT_PREFIX}.replay.stdout"
: > "${OUT_PREFIX}.replay.stderr"

record_server_restart() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  local exit_code="$5"
  local action="$6"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DBMS" "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "$action" \
    >> "$RESTART_LOG"
}

maybe_recover_server() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  local exit_code="$5"
  DBMS_SEED_INDEX=$((DBMS_SEED_INDEX + 1))
  if [[ "$exit_code" -eq 0 && $((DBMS_SEED_INDEX % SERVER_CHECK_INTERVAL)) -ne 0 ]]; then
    return 0
  fi
  if server_alive; then
    return 0
  fi
  record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "server_missing_restart"
  if ! reset_server "restart"; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_failed"
    exit 6
  fi
  if ! wait_for_server; then
    record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_no_server"
    exit 6
  fi
  record_server_restart "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$exit_code" "restart_ok"
}

run_dbms_seed() {
  local checkpoint_min="$1"
  local seed_time="$2"
  local seed_name="$3"
  local seed="$4"
  set +e
  timeout "$SEED_TIMEOUT" "$TEST_SCRIPT" < "$seed" >> "${OUT_PREFIX}.replay.stdout" 2>> "${OUT_PREFIX}.replay.stderr"
  local rc=$?
  set -e
  local status=success
  if [[ "$rc" -eq 124 ]]; then
    status=timeout
  elif [[ "$rc" -ne 0 ]]; then
    status=failed
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$rc" "$status" >> "$SEED_STATUS"
  maybe_recover_server "$checkpoint_min" "$seed_time" "$seed_name" "$seed" "$rc"
}

snapshot_coverage() {
  local cp_min="$1"
  local prefix="${OUT_PREFIX}_t${cp_min}"
  local cumulative="/tmp/rq2_cumulative_${cp_min}.tsv"
  cp "$cumulative" "${prefix}.executed_seeds.tsv"
  find "$PROFILE_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -ls > "${prefix}.profiles.list" || true
  shopt -s nullglob
  local profiles=("$PROFILE_DIR"/*.profraw "$PROFILE_DIR"/*.profdata)
  if [[ ${#profiles[@]} -eq 0 ]]; then
    echo "no LLVM profile files were generated at checkpoint ${cp_min}" >&2
    exit 5
  fi
  "$LLVM_PROFDATA_BIN" merge -sparse "${profiles[@]}" -o "${prefix}.profdata"
  "$LLVM_COV_BIN" export -format=text "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.cov.json"
  "$LLVM_COV_BIN" report "$BINARY" -instr-profile="${prefix}.profdata" > "${prefix}.report.txt"
  local seed_count success_count failure_count timeout_count restart_count profile_count
  seed_count=$(tail -n +2 "$SEED_STATUS" | wc -l | tr -d ' ')
  success_count=$(awk -F'\t' 'NR>1 && $7=="success"{n++} END{print n+0}' "$SEED_STATUS")
  timeout_count=$(awk -F'\t' 'NR>1 && $7=="timeout"{n++} END{print n+0}' "$SEED_STATUS")
  failure_count=$(awk -F'\t' 'NR>1 && $7!="success"{n++} END{print n+0}' "$SEED_STATUS")
  restart_count=$(awk -F'\t' 'NR>1 && $8=="restart_ok"{n++} END{print n+0}' "$RESTART_LOG")
  profile_count=${#profiles[@]}
  {
    printf 'seed_count\t%s\n' "$seed_count"
    printf 'success_count\t%s\n' "$success_count"
    printf 'failure_count\t%s\n' "$failure_count"
    printf 'timeout_count\t%s\n' "$timeout_count"
    printf 'server_restart_count\t%s\n' "$restart_count"
    printf 'profile_count\t%s\n' "$profile_count"
  } > "${prefix}.meta.tsv"
}

if [[ ! -x "$RESET_SCRIPT" || ! -x "$TEST_SCRIPT" ]]; then
  echo "missing reset/test script: $RESET_SCRIPT $TEST_SCRIPT" >&2
  exit 3
fi
if ! reset_server "initial" || ! wait_for_server; then
  echo "initial reset failed or server did not start" >&2
  exit 3
fi
for cp_ms in "${CHECKPOINT_ARR[@]}"; do
  cp_min=$((cp_ms / 60000))
  bucket="/tmp/rq2_bucket_${cp_min}.tsv"
  while IFS=$'\t' read -r seed_time seed_name seed; do
    [[ -n "${seed:-}" ]] || continue
    run_dbms_seed "$cp_min" "$seed_time" "$seed_name" "$seed"
  done < "$bucket"
  snapshot_coverage "$cp_min"
done
terminate_server
RUNNER_SH
chmod +x "$RUNNER"

PREFLIGHT=$DEST/preflight_status.tsv
printf 'dbms\timage\tbinary\tstatus\tmessage\n' > "$PREFLIGHT"
if docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "command -v llvm-profdata-12 >/dev/null && command -v llvm-cov-12 >/dev/null && test -x '$BINARY' && strings '$BINARY' | grep -q __llvm_prf && test -x '$RESET_SCRIPT' && test -x '$TEST_SCRIPT'"; then
  printf 'postgres\t%s\t%s\tok\tLLVM source coverage backend ready\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
else
  printf 'postgres\t%s\t%s\tfailed\tLLVM source coverage backend/reset/test not usable\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
  exit 4
fi

rm -rf "$REPLAY_DIR"
mkdir -p "$REPLAY_DIR"

REPLAY_START_EPOCH=$(date +%s)
REPLAY_START_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CONTAINER=sqleek_pg_stage3_r1_pilot_replay_$(date -u +%Y%m%d_%H%M%S)
HOST_PREFIX=$REPLAY_DIR/$RUN_ID
CONTAINER_PREFIX=/rq2_out/$RUN_ID

status=complete
message=
if docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
    -e GRIFFIN_CONTAINER=1 \
    -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
    -e RQ2_REPLAY_SERVER_CHECK_INTERVAL="${RQ2_REPLAY_SERVER_CHECK_INTERVAL:-1}" \
    -v "$REPLAY_QUEUE":/rq2_queue:ro \
    -v "$REPLAY_DIR":/rq2_out \
    -v "$RUNNER":/runner.sh:ro \
    --name "$CONTAINER" --entrypoint /bin/bash "$IMAGE" \
    /runner.sh \
      --dbms postgres --binary "$BINARY" --checkpoints-ms "$CHECKPOINT_MS" \
      --seed-timeout "$SEED_TIMEOUT" --process-name pg_c_8888 --out-prefix "$CONTAINER_PREFIX" \
      --reset-script "$RESET_SCRIPT" --test-script "$TEST_SCRIPT" \
      > "$LOG_DIR/docker_replay.log" 2>&1; then
  status=complete
else
  status=failed
  message="docker replay failed; inspect $LOG_DIR/docker_replay.log"
fi
REPLAY_DOCKER_END_EPOCH=$(date +%s)
REPLAY_END_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)

META=$HOST_PREFIX"_t${CHECKPOINT_MIN}.meta.tsv"
seed_count=0
success_count=0
failure_count=0
timeout_count=0
server_restart_count=0
if [[ -f "$META" ]]; then
  seed_count=$(awk -F'\t' '$1=="seed_count"{print $2}' "$META")
  success_count=$(awk -F'\t' '$1=="success_count"{print $2}' "$META")
  failure_count=$(awk -F'\t' '$1=="failure_count"{print $2}' "$META")
  timeout_count=$(awk -F'\t' '$1=="timeout_count"{print $2}' "$META")
  server_restart_count=$(awk -F'\t' '$1=="server_restart_count"{print $2}' "$META")
fi

RQ2_INDEX=$DEST/rq2_replay_index.tsv
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$RQ2_INDEX"
cov_json=$HOST_PREFIX"_t${CHECKPOINT_MIN}.cov.json"
report_txt=$HOST_PREFIX"_t${CHECKPOINT_MIN}.report.txt"
if [[ "$status" == complete && ! -f "$cov_json" ]]; then
  status=failed
  message="missing cov_json after replay"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$RUN_ID" "$TOOL" "$DBMS" "$REPEAT" "$CHECKPOINT_MIN" "$cov_json" "$report_txt" "$status" "$message" \
  "$IMAGE" "$BINARY" "$seed_count" "$REPLAY_QUEUE" "$IMAGE" "$SRC_CONTAINER" "PostgreSQL LLVM pilot from live SQLeek Stage3 r1" "$REPLAY_START_UTC" "$REPLAY_END_UTC" >> "$RQ2_INDEX"

if [[ "$status" == complete ]]; then
  python3 "$RQ2_REPLAY/summarize_llvm_cov.py" \
    --target-regions "$DEST/target_regions.csv" \
    --replay-index "$RQ2_INDEX" \
    --out "$DATA_DIR" \
    --tool "$TOOL" > "$LOG_DIR/summarize_llvm_cov.log" 2>&1
fi
SUMMARY_END_EPOCH=$(date +%s)

python3 - "$DEST" "$CHECKPOINT_MIN" "$status" "$message" "$RUN_STATUS" "$RUN_HEALTH" "$REPLAY_START_EPOCH" "$REPLAY_DOCKER_END_EPOCH" "$SUMMARY_END_EPOCH" "$success_count" "$failure_count" "$timeout_count" "$server_restart_count" <<'PY'
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path

dest = Path(sys.argv[1])
checkpoint_min = int(sys.argv[2])
status = sys.argv[3]
message = sys.argv[4]
run_status = sys.argv[5]
run_health = sys.argv[6]
replay_start = int(sys.argv[7])
docker_end = int(sys.argv[8])
summary_end = int(sys.argv[9])
success_count = int(sys.argv[10] or 0)
failure_count = int(sys.argv[11] or 0)
timeout_count = int(sys.argv[12] or 0)
server_restart_count = int(sys.argv[13] or 0)

summary = json.loads((dest / "summary.json").read_text())
cov_rows = []
cov_path = dest / "data" / "coverage_summary.csv"
if cov_path.exists():
    with cov_path.open(newline="") as fp:
        cov_rows = list(csv.DictReader(fp))
cov = cov_rows[0] if cov_rows else {}

def as_int(key: str, default: int = 0) -> int:
    try:
        return int(float(cov.get(key, default)))
    except Exception:
        return default

def as_float(key: str, default: float = 0.0) -> float:
    try:
        return float(cov.get(key, default))
    except Exception:
        return default

target_branches_total = as_int("risk_branches_total")
target_branches_hit = as_int("risk_branches_hit")
target_regions_total = as_int("risk_targets_total", int(summary.get("target_regions_count", 0)))
target_regions_hit = as_int("risk_targets_hit")
global_branches_total = as_int("global_branches_total")
global_branches_hit = as_int("global_branches_hit")
success_rate = success_count / (success_count + failure_count) if (success_count + failure_count) else 0.0

target_regions_file_count = 0
tr = dest / "target_regions.csv"
if tr.exists():
    with tr.open(newline="") as fp:
        target_regions_file_count = max(0, sum(1 for _ in fp) - 1)
if not target_regions_total:
    target_regions_total = target_regions_file_count

issues = []
if summary.get("mtime_mismatch_count", 0):
    issues.append(f"mtime mismatch count: {summary['mtime_mismatch_count']}")
if summary.get("missing_source_after_copy_count", 0):
    issues.append(f"source files missing after copy: {summary['missing_source_after_copy_count']}")
if status != "complete":
    issues.append(message or "replay failed")
if not issues:
    issues.append("No queue mtime, path, or replay compatibility issue observed in this pilot.")

result = {
    **summary,
    "stage3_container_status": run_status,
    "stage3_container_health": run_health,
    "replay_status": status,
    "replay_message": message,
    "replay_success_count": success_count,
    "replay_failure_count": failure_count,
    "replay_timeout_count": timeout_count,
    "replay_success_rate": success_rate,
    "server_restart_count": server_restart_count,
    "docker_replay_seconds": docker_end - replay_start,
    "total_replay_and_summary_seconds": summary_end - replay_start,
    "target_branches_total": target_branches_total,
    "target_branches_hit": target_branches_hit,
    "target_branch_coverage": as_float("target_region_branch_coverage"),
    "target_regions_total": target_regions_total,
    "target_regions_hit": target_regions_hit,
    "target_region_hit_rate": as_float("target_function_hit_rate"),
    "global_branches_total": global_branches_total,
    "global_branches_hit": global_branches_hit,
    "global_branch_coverage": as_float("global_branch_coverage"),
    "issues": issues,
}
(dest / "replay_results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def pct(v: float) -> str:
    return f"{v * 100:.2f}%"

md = []
md.append("# SQLeek Stage3 PostgreSQL r1 Queue Pilot Replay")
md.append("")
md.append("This is a pilot checkpoint/replay only. It was not merged into the formal RQ2 tables or figures.")
md.append("")
md.append("## Source")
md.append("")
md.append(f"- Source DBMS: PostgreSQL")
md.append(f"- Source queue: `{summary['source_queue']}`")
md.append(f"- Source container: `{summary['source_container']}` ({run_status}, {run_health})")
md.append(f"- Checkpoint path: `{summary['checkpoint_path']}`")
md.append(f"- Replay checkpoint: {checkpoint_min} min")
md.append("")
md.append("## Checkpoint Manifest Stats")
md.append("")
md.append(f"- Queue total: {summary['queue_total']}")
md.append(f"- Initial corpus: {summary['initial_corpus_count']}")
md.append(f"- New queue: {summary['new_queue_count']}")
md.append(f"- hi_val_online_score inputs: {summary['hi_val_count']}")
md.append(f"- Total size: {summary['total_size_bytes']} bytes")
md.append(f"- Replay-selected inputs at {checkpoint_min} min: {summary['replay_selected_count']}")
md.append(f"- mtime mismatch count: {summary['mtime_mismatch_count']}")
md.append("")
md.append("## Replay")
md.append("")
md.append(f"- Status: {status}")
if message:
    md.append(f"- Message: {message}")
md.append(f"- Successful inputs: {success_count}")
md.append(f"- Failed inputs: {failure_count}")
md.append(f"- Timeout inputs: {timeout_count}")
md.append(f"- Success rate: {pct(success_rate)}")
md.append(f"- Server restarts during replay: {server_restart_count}")
md.append(f"- Docker replay time: {docker_end - replay_start} s")
md.append(f"- Total replay + summary time: {summary_end - replay_start} s")
md.append("")
md.append("## Coverage")
md.append("")
md.append(f"- Target branches: {target_branches_hit}/{target_branches_total} ({pct(as_float('target_region_branch_coverage'))})")
md.append(f"- Target regions: {target_regions_hit}/{target_regions_total} ({pct(as_float('target_function_hit_rate'))})")
md.append(f"- Global branches: {global_branches_hit}/{global_branches_total} ({pct(as_float('global_branch_coverage'))})")
md.append("")
md.append("## Files")
md.append("")
md.append(f"- Manifest JSONL: `{dest / 'manifest.jsonl'}`")
md.append(f"- Manifest TSV: `{dest / 'manifest.tsv'}`")
md.append(f"- Coverage summary: `{dest / 'data' / 'coverage_summary.csv'}`")
md.append(f"- Replay index: `{dest / 'rq2_replay_index.tsv'}`")
md.append(f"- Replay raw output: `{dest / 'replay_out' / 'pilot_current'}`")
md.append("")
md.append("## Compatibility Notes")
md.append("")
for issue in issues:
    md.append(f"- {issue}")
md.append("")
(dest / "REPLAY_RESULTS.md").write_text("\n".join(md), encoding="utf-8")
PY

if [[ -f "$cov_json" ]]; then
  printf '%s\t%s\n' "$cov_json" "$(stat -c '%s' "$cov_json")" > "$DEST/deleted_cov_json.tsv"
  rm -f "$cov_json"
fi

sha256sum "$DEST/manifest.jsonl" "$DEST/manifest.tsv" "$DEST/replay_index.tsv" "$DEST/summary.json" "$DEST/target_regions.csv" "$DEST/REPLAY_RESULTS.md" > "$DEST/artifacts.sha256"

cat "$DEST/REPLAY_RESULTS.md"
