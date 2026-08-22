#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=/root/SQLeek/experiment/RQ2/replay
RQ2_DIR=/root/SQLeek/experiment/RQ2
OUT_ROOT="$SCRIPT_DIR/output"
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=${OUT:-$OUT_ROOT/sqlaser_postgres_norec_5x24h_replay_$TS}
CONTAINER=${SQLASER_PG_CONTAINER:-sqlright_postgres_NOREC}
IMAGE=${RQ2_POSTGRES_IMAGE:-griffin_postgres_llvmcov}
BINARY=${RQ2_POSTGRES_BINARY:-/root/bin_aflpp/usr/local/pgsql/bin/postgres}
RESET_SCRIPT=${RQ2_POSTGRES_RESET_SCRIPT:-/workspace/scripts/reset_lv1.sh}
TEST_SCRIPT=${RQ2_POSTGRES_TEST_SCRIPT:-/workspace/scripts/testt}
CHECKPOINTS_MIN=${RQ2_CHECKPOINTS_MIN:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${RQ2_SEED_TIMEOUT:-60}
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-120G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-4G}
TOOL=SQLaser
DBMS=postgres
VERSION="PostgreSQL llvmcov SQLaser replay"
SOURCE_ROOT_IN_CONTAINER=/home/postgres/fuzzing/fuzz_root/outputs

mkdir -p "$OUT" "$OUT/logs" "$OUT/work" "$OUT/raw" "$OUT/data"
LOG="$OUT/driver.log"
exec > >(tee -a "$LOG") 2>&1

echo "out=$OUT"
echo "container=$CONTAINER"
echo "image=$IMAGE"
echo "binary=$BINARY"
echo "checkpoints_min=$CHECKPOINTS_MIN"
echo "seed_timeout=$SEED_TIMEOUT"
date -u -Is
printf '%s\n' "$OUT" > /tmp/rq2_sqlaser_postgres_norec_latest.path

wait_for_fuzz_done() {
  echo "waiting_for_sqlaser_pg_fuzz_to_finish=$(date -u -Is)"
  while true; do
    if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
      echo "container $CONTAINER is not running; assuming fuzzing finished or container stopped"
      break
    fi
    local afl_count
    afl_count=$(docker exec "$CONTAINER" bash -lc "ps -eo args | grep -F './afl-fuzz -t 2000' | grep -F 'outputs/outputs_' | grep -v grep | wc -l" 2>/dev/null || echo 0)
    local pg_count
    pg_count=$(docker exec "$CONTAINER" bash -lc "ps -eo args | grep -F '/home/postgres/postgres/bld/bin/postgres -D /home/postgres/postgres/bld/data_all/data_' | grep -v grep | wc -l" 2>/dev/null || echo 0)
    echo "$(date -u -Is) live_sqlaser_afl=$afl_count live_sqlaser_postgres=$pg_count"
    if [[ "$afl_count" == "0" ]]; then
      break
    fi
    sleep 300
  done
  echo "fuzz_done_observed=$(date -u -Is); waiting 120s for filesystem quiescence"
  sleep 120
}

copy_one_run() {
  local idx="$1"
  local repeat="$2"
  local src="$SOURCE_ROOT_IN_CONTAINER/outputs_${idx}"
  local dst="$OUT/raw/outputs_${idx}"
  mkdir -p "$dst"
  echo "copy raw r${repeat}: $src -> $dst"
  docker exec "$CONTAINER" tar --ignore-failed-read -C "$src" -cf - \
    queue crashes hangs fuzzer_stats plot_data fuzz_bitmap .cur_input 2>"$OUT/logs/copy_r${repeat}.tar.stderr" \
    | tar -C "$dst" -xf -
  find "$dst" -maxdepth 2 -type f | wc -l | awk -v r="r${repeat}" '{print r" copied_files="$1}'
}

prepare_queue() {
  local repeat="$1"
  local src="$2"
  local dst="$3"
  local stats="$4"
  mkdir -p "$dst"
  python3 - "$repeat" "$src" "$dst" "$stats" "$OUT/work/postgres_sqlaser_r${repeat}/queue_time_mapping.tsv" <<'PY'
from __future__ import annotations
import os
import re
import shutil
import sys
from pathlib import Path

repeat = sys.argv[1]
src = Path(sys.argv[2])
dst = Path(sys.argv[3])
stats_path = Path(sys.argv[4])
manifest = Path(sys.argv[5])
if not src.is_dir():
    raise SystemExit(f"missing queue dir: {src}")
# SQLaser PG queue names do not contain AFL time: metadata.  fuzzer_stats
# start_time may be overwritten by a later AFL resume, which would clamp all
# pre-resume seeds to elapsed 0.  Use queue file mtimes as the stable first-seen
# source and anchor elapsed time at the earliest queue mtime.
queue_files_for_start = [q for q in src.iterdir() if q.is_file() and q.name != "README.txt" and not q.name.startswith(".")]
start = min((q.stat().st_mtime for q in queue_files_for_start), default=0.0)
rows = []
for p in sorted(src.iterdir()):
    if not p.is_file():
        continue
    name = p.name
    if name == "README.txt" or name.startswith("."):
        continue
    elapsed_ms = None
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", name)
    if m:
        elapsed_ms = int(m.group(1))
    elif re.search(r"(?:^|,)orig:", name):
        elapsed_ms = 0
    else:
        elapsed_ms = max(0, int(round((p.stat().st_mtime - start) * 1000)))
    mid = re.match(r"id:([0-9]+)", name)
    sid = int(mid.group(1)) if mid else len(rows)
    rows.append((elapsed_ms, sid, name, p, p.stat().st_size, p.stat().st_mtime))
rows.sort(key=lambda x: (x[0], x[1], x[2]))
manifest.parent.mkdir(parents=True, exist_ok=True)
with manifest.open("w", encoding="utf-8") as fp:
    fp.write("repeat\toriginal\treplay_name\telapsed_ms\toriginal_mtime\tsize\tmode\n")
    for seq, (elapsed_ms, sid, name, p, size, mtime) in enumerate(rows, start=1):
        safe_name = name if re.search(r"(?:^|,)time:\d+(?:,|$)", name) else f"time:{elapsed_ms},seq:{seq:06d},{name}"
        out = dst / safe_name
        if out.exists() or out.is_symlink():
            out.unlink()
        try:
            os.link(p, out)
            mode = "hardlink"
        except OSError:
            shutil.copy2(p, out)
            mode = "copy2"
        fp.write(f"{repeat}\t{name}\t{safe_name}\t{elapsed_ms}\t{mtime:.6f}\t{size}\t{mode}\n")
print(f"repeat={repeat} seed_count={len(rows)} start_epoch={start} max_elapsed_ms={(rows[-1][0] if rows else 0)} queue_time_named={dst}")
PY
}

append_stats_csv() {
  local data_dir="$1"
  python3 - "$data_dir" <<'PY'
from __future__ import annotations
import csv, math, sys
from collections import defaultdict
from pathlib import Path
base = Path(sys.argv[1])
rows = []
path = base / "coverage_timeseries.csv"
if path.exists():
    with path.open(newline="") as fp:
        rows = list(csv.DictReader(fp))
groups = defaultdict(list)
for r in rows:
    groups[(r.get("tool", ""), r.get("dbms", ""), r.get("elapsed_min", ""))].append(r)
fields = ["tool","dbms","elapsed_min","n","mean_target_region_branch_coverage","std_target_region_branch_coverage","se_target_region_branch_coverage","ci95_target_region_branch_coverage","mean_risk_branches_hit","std_risk_branches_hit","mean_risk_targets_hit","std_risk_targets_hit","mean_global_branch_coverage","std_global_branch_coverage","se_global_branch_coverage","ci95_global_branch_coverage"]
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def std(xs):
    if len(xs) < 2: return 0.0
    m = mean(xs)
    return math.sqrt(sum((x-m)**2 for x in xs)/(len(xs)-1))
with (base / "coverage_timeseries_stats.csv").open("w", newline="") as fp:
    w = csv.DictWriter(fp, fieldnames=fields); w.writeheader()
    for (tool, dbms, elapsed), vals in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0], int(kv[0][2] or 0))):
        cov=[float(v.get("target_region_branch_coverage") or 0) for v in vals]
        rb=[float(v.get("risk_branches_hit") or 0) for v in vals]
        rt=[float(v.get("risk_targets_hit") or 0) for v in vals]
        gc=[float(v.get("global_branch_coverage") or 0) for v in vals]
        n=len(vals); scov=std(cov); sgc=std(gc)
        w.writerow({"tool":tool,"dbms":dbms,"elapsed_min":elapsed,"n":n,"mean_target_region_branch_coverage":mean(cov),"std_target_region_branch_coverage":scov,"se_target_region_branch_coverage":scov/math.sqrt(n) if n else 0.0,"ci95_target_region_branch_coverage":1.96*scov/math.sqrt(n) if n else 0.0,"mean_risk_branches_hit":mean(rb),"std_risk_branches_hit":std(rb),"mean_risk_targets_hit":mean(rt),"std_risk_targets_hit":std(rt),"mean_global_branch_coverage":mean(gc),"std_global_branch_coverage":sgc,"se_global_branch_coverage":sgc/math.sqrt(n) if n else 0.0,"ci95_global_branch_coverage":1.96*sgc/math.sqrt(n) if n else 0.0})
PY
}

wait_for_fuzz_done

if ! docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
  echo "missing container $CONTAINER" >&2
  exit 2
fi

for idx in 0 1 2 3 4; do
  copy_one_run "$idx" "$((idx+1))"
done

docker exec "$CONTAINER" bash -lc "for d in $SOURCE_ROOT_IN_CONTAINER/outputs_*; do [ -d \"\$d\" ] || continue; printf '%s queue=' \"\$d\"; find \"\$d/queue\" -maxdepth 1 -type f 2>/dev/null | wc -l; done" | tee "$OUT/raw_queue_counts.txt"

python3 "$SCRIPT_DIR/build_target_regions.py" --dbms postgres --out "$OUT/target_regions.csv"

PREFLIGHT="$OUT/preflight_status.tsv"
MASTER_INDEX="$OUT/replay_index.tsv"
printf 'dbms\timage\tbinary\tstatus\tmessage\n' > "$PREFLIGHT"
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$MASTER_INDEX"

if docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "command -v llvm-profdata-12 >/dev/null && command -v llvm-cov-12 >/dev/null && test -x '$BINARY' && strings '$BINARY' | grep -q __llvm_prf && test -x '$RESET_SCRIPT' && test -x '$TEST_SCRIPT'"; then
  printf 'postgres\t%s\t%s\tok\tLLVM source coverage backend ready\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
else
  printf 'postgres\t%s\t%s\tmissing\tLLVM source coverage backend/reset/test not usable\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
  echo "preflight failed; see $PREFLIGHT" >&2
  exit 1
fi

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MIN"
CHECKPOINTS_MS=()
for cp in "${CHECKPOINT_ARR[@]}"; do
  cp=${cp//[[:space:]]/}
  [[ -n "$cp" ]] || continue
  CHECKPOINTS_MS+=("$((cp * 60 * 1000))")
done
CHECKPOINTS_MS_CSV=$(IFS=','; echo "${CHECKPOINTS_MS[*]}")

for repeat in ${SQLASER_PG_REPEATS:-1}; do
  idx=$((repeat-1))
  RUN_ID="postgres_sqlaser_r${repeat}"
  RAW_RUN="$OUT/raw/outputs_${idx}"
  QUEUE_DIR="$RAW_RUN/queue"
  run_work="$OUT/work/$RUN_ID"
  QUEUE_NAMED="$run_work/queue_time_named"
  mkdir -p "$run_work/out"
  prepare_queue "$repeat" "$QUEUE_DIR" "$QUEUE_NAMED" "$RAW_RUN/fuzzer_stats"

  seed_total=$(find "$QUEUE_NAMED" -maxdepth 1 -type f | wc -l | tr -d ' ')
  start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  prefix="/rq2_out/${RUN_ID}"
  host_prefix="$run_work/out/${RUN_ID}"
  name="rq2_sqlaser_pg_r${repeat}_${TS}_${RANDOM}"
  status=complete
  message=
  echo "[$(date -Is)] start $RUN_ID seeds=$seed_total queue=$QUEUE_NAMED"

  if docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
      -e GRIFFIN_CONTAINER=1 \
      -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
      -e RQ2_REPLAY_SERVER_CHECK_INTERVAL="${RQ2_REPLAY_SERVER_CHECK_INTERVAL:-1}" \
      -v "$SCRIPT_DIR":/rq2_scripts:ro \
      -v "$QUEUE_NAMED":/rq2_queue:ro \
      -v "$run_work/out":/rq2_out \
      --name "$name" --entrypoint /bin/bash "$IMAGE" \
      /rq2_scripts/container_replay_llvm_bucketed.sh \
        --dbms postgres --binary "$BINARY" --checkpoints-ms "$CHECKPOINTS_MS_CSV" \
        --seed-timeout "$SEED_TIMEOUT" --process-name pg_c_8888 --out-prefix "$prefix" \
        --reset-script "$RESET_SCRIPT" --test-script "$TEST_SCRIPT" \
        > "$OUT/logs/${RUN_ID}.docker.log" 2>&1; then
    status=complete
  else
    status=failed
    message="docker bucketed replay failed; inspect $OUT/logs/${RUN_ID}.docker.log and $run_work/out"
  fi
  end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  for cp in "${CHECKPOINT_ARR[@]}"; do
    cp=${cp//[[:space:]]/}
    [[ -n "$cp" ]] || continue
    cov_json="${host_prefix}_t${cp}.cov.json"
    report_txt="${host_prefix}_t${cp}.report.txt"
    meta="${host_prefix}_t${cp}.meta.tsv"
    cp_seed_count="$seed_total"
    if [[ -f "$meta" ]]; then
      cp_seed_count=$(awk -F'\t' '$1=="seed_count"{print $2}' "$meta")
    fi
    row_status="$status"
    row_message="$message"
    if [[ "$status" == complete && ! -f "${host_prefix}_t${cp}.profdata" ]]; then
      row_status=failed
      row_message="missing profdata for checkpoint $cp"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$RUN_ID" "$TOOL" "$DBMS" "$repeat" "$cp" "$cov_json" "$report_txt" "$row_status" "$row_message" \
      "$IMAGE" "$BINARY" "$cp_seed_count" "$QUEUE_DIR" "$IMAGE" "sqlaser_postgres_norec_outputs_${idx}" "$VERSION" "$start_time" "$end_time" >> "$MASTER_INDEX"
  done

  echo "[$(date -Is)] done $RUN_ID status=$status"
  if [[ "$status" != complete ]]; then
    echo "$message" >&2
    exit 3
  fi
  find "$run_work/out" -name '*.cov.json' -type f -printf '%p\t%s\n' >> "$OUT/deleted_cov_json.tsv" || true
  find "$run_work/out" -name '*.cov.json' -type f -delete || true
done

python3 /root/SQLeek/experiment/RQ2/scripts/resummarize_postgres_from_profdata.py \
  --run-dir "$OUT" \
  --out "$OUT/data" \
  --target-regions "$OUT/target_regions.csv" \
  --tool "$TOOL" \
  --image "$IMAGE" \
  --binary "$BINARY"

append_stats_csv "$OUT/data"

{
  echo "out=$OUT"
  echo "container=$CONTAINER"
  echo "image=$IMAGE"
  echo "binary=$BINARY"
  echo "checkpoints_min=$CHECKPOINTS_MIN"
  echo "seed_timeout=$SEED_TIMEOUT"
  echo "data_dir=$OUT/data"
  echo "status=complete"
  echo "finished_utc=$(date -u -Is)"
} > "$OUT/REPLAY_SUMMARY.txt"

echo "completed_sqlaser_postgres_replay=$OUT"
