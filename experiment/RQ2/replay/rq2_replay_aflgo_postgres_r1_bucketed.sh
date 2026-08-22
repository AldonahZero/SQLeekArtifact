#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=/root/SQLeek/experiment/RQ2/replay
OUT_ROOT=/root/SQLeek/experiment/RQ2/replay/output
TS=$(date -u +%Y%m%d_%H%M%S)
OUT="$OUT_ROOT/aflgo_postgres_r1_replay_${TS}"
IMAGE=${RQ2_POSTGRES_IMAGE:-griffin_postgres_llvmcov}
BINARY=${RQ2_POSTGRES_BINARY:-/root/bin_aflpp/usr/local/pgsql/bin/postgres}
RESET_SCRIPT=${RQ2_POSTGRES_RESET_SCRIPT:-/workspace/scripts/reset_lv1.sh}
TEST_SCRIPT=${RQ2_POSTGRES_TEST_SCRIPT:-/workspace/scripts/testt}
QUEUE_DIR=${RQ2_AFLGO_POSTGRES_R1_QUEUE:-/root/SQLeek/experiment/RQ2/aflgo/postgres/output_rq2_24h/postgres_aflgo_r1_20260708_103840/postgres_aflgo_r1/postgres_aflgo_r1/queue}
CHECKPOINTS_MIN=${RQ2_CHECKPOINTS_MIN:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${RQ2_SEED_TIMEOUT:-60}
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-120G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-4G}
TOOL=AFLGo
DBMS=postgres
REPEAT=1
RUN_ID=postgres_aflgo_r1
SOURCE_NAME=postgres_aflgo_r1_20260708_103840

mkdir -p "$OUT"/{work,logs,data}
echo "$OUT" > /tmp/rq2_aflgo_postgres_r1_latest.path

if [[ ! -d "$QUEUE_DIR" ]]; then
  echo "missing queue: $QUEUE_DIR" >&2
  exit 2
fi

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

QUEUE_NAMED="$OUT/work/${RUN_ID}_queue_time_named"
mkdir -p "$QUEUE_NAMED"
python3 - "$QUEUE_DIR" "$QUEUE_NAMED" <<'PY'
from __future__ import annotations
import os
import re
import shutil
import sys
from pathlib import Path
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
rows = []
for p in sorted(src.iterdir()):
    if not p.is_file():
        continue
    name = p.name
    t = None
    if re.search(r'(?:^|,)orig:', name):
        t = 0
    m = re.search(r'(?:^|,)time:(\d+)(?:,|$)', name)
    if m:
        t = int(m.group(1))
    if t is None:
        m = re.match(r'id:(\d+),(\d+)(?:,|$)', name)
        if m:
            t = int(m.group(2))
    if t is None:
        t = 0
    new_name = name if re.search(r'(?:^|,)time:\d+(?:,|$)', name) else f'{name},time:{t}'
    out = dst / new_name
    if out.exists() or out.is_symlink():
        out.unlink()
    try:
        os.link(p, out)
        mode = 'hardlink'
    except OSError:
        shutil.copy2(p, out)
        mode = 'copy2'
    rows.append((name, new_name, t, mode))
with (dst.parent / 'queue_time_mapping.tsv').open('w', encoding='utf-8') as fp:
    fp.write('original\treplay_name\ttime_ms\tmode\n')
    for row in rows:
        fp.write('\t'.join(map(str,row)) + '\n')
print(f'queue_time_named={dst}')
print(f'seed_count={len(rows)}')
PY

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MIN"
CHECKPOINTS_MS=()
for cp in "${CHECKPOINT_ARR[@]}"; do
  CHECKPOINTS_MS+=("$((cp * 60 * 1000))")
done
CHECKPOINTS_MS_CSV=$(IFS=','; echo "${CHECKPOINTS_MS[*]}")

seed_total=$(find "$QUEUE_NAMED" -maxdepth 1 -type f | wc -l | tr -d ' ')
start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run_work="$OUT/work/$RUN_ID"
mkdir -p "$run_work/out"
prefix="/rq2_out/${RUN_ID}"
host_prefix="$run_work/out/${RUN_ID}"
name="rq2_aflgo_pg_r1_${TS}_${RANDOM}"
status=complete
message=
echo "[$(date -Is)] start $RUN_ID seeds=$seed_total queue=$QUEUE_NAMED out=$OUT"

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
    "$RUN_ID" "$TOOL" "$DBMS" "$REPEAT" "$cp" "$cov_json" "$report_txt" "$row_status" "$row_message" \
    "$IMAGE" "$BINARY" "$cp_seed_count" "$QUEUE_DIR" "$IMAGE" "$SOURCE_NAME" "PostgreSQL llvmcov AFLGo replay" "$start_time" "$end_time" >> "$MASTER_INDEX"
done

if [[ "$status" != complete ]]; then
  echo "$message" >&2
  exit 3
fi

python3 /root/SQLeek/experiment/RQ2/scripts/resummarize_postgres_from_profdata.py \
  --run-dir "$OUT" \
  --out "$OUT/data" \
  --target-regions "$OUT/target_regions.csv" \
  --tool AFLGo \
  --image "$IMAGE"

python3 - "$OUT/data" <<'PY'
from __future__ import annotations
import csv, math, sys
from collections import defaultdict
from pathlib import Path
base = Path(sys.argv[1])
ts = base / 'coverage_timeseries.csv'
rows = []
if ts.exists():
    with ts.open(newline='') as fp:
        rows = list(csv.DictReader(fp))
groups = defaultdict(list)
for r in rows:
    groups[(r.get('tool',''), r.get('dbms',''), r.get('elapsed_min',''))].append(r)
fields = ['tool','dbms','elapsed_min','n','mean_target_region_branch_coverage','std_target_region_branch_coverage','se_target_region_branch_coverage','ci95_target_region_branch_coverage','mean_risk_branches_hit','std_risk_branches_hit','mean_risk_targets_hit','std_risk_targets_hit','mean_global_branch_coverage','std_global_branch_coverage','se_global_branch_coverage','ci95_global_branch_coverage']
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def std(xs):
    if len(xs) < 2: return 0.0
    m = mean(xs)
    return math.sqrt(sum((x-m)**2 for x in xs)/(len(xs)-1))
with (base / 'coverage_timeseries_stats.csv').open('w', newline='') as fp:
    w = csv.DictWriter(fp, fieldnames=fields); w.writeheader()
    for (tool, dbms, elapsed), vals in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0], int(kv[0][2] or 0))):
        cov = [float(v.get('target_region_branch_coverage') or 0) for v in vals]
        rb = [float(v.get('risk_branches_hit') or 0) for v in vals]
        rt = [float(v.get('risk_targets_hit') or 0) for v in vals]
        gc = [float(v.get('global_branch_coverage') or 0) for v in vals]
        n = len(vals)
        scov, sgc = std(cov), std(gc)
        w.writerow({'tool':tool,'dbms':dbms,'elapsed_min':elapsed,'n':n,
          'mean_target_region_branch_coverage':mean(cov),'std_target_region_branch_coverage':scov,
          'se_target_region_branch_coverage':scov/math.sqrt(n) if n else 0.0,'ci95_target_region_branch_coverage':1.96*scov/math.sqrt(n) if n else 0.0,
          'mean_risk_branches_hit':mean(rb),'std_risk_branches_hit':std(rb),
          'mean_risk_targets_hit':mean(rt),'std_risk_targets_hit':std(rt),
          'mean_global_branch_coverage':mean(gc),'std_global_branch_coverage':sgc,
          'se_global_branch_coverage':sgc/math.sqrt(n) if n else 0.0,'ci95_global_branch_coverage':1.96*sgc/math.sqrt(n) if n else 0.0})
PY

{
  echo "out=$OUT"
  echo "queue_dir=$QUEUE_DIR"
  echo "queue_time_named=$QUEUE_NAMED"
  echo "seed_count=$seed_total"
  echo "start_time=$start_time"
  echo "end_time=$end_time"
  echo "status=$status"
  echo "image=$IMAGE"
  echo "binary=$BINARY"
  echo "reset_script=$RESET_SCRIPT"
  echo "test_script=$TEST_SCRIPT"
  echo "checkpoints_min=$CHECKPOINTS_MIN"
  echo "data_dir=$OUT/data"
} > "$OUT/REPLAY_SUMMARY.txt"

# Keep profdata and reports; remove huge intermediate cov.json after CSV validation to reduce disk pressure.
find "$run_work/out" -name '*.cov.json' -type f -printf '%p\t%s\n' > "$OUT/deleted_cov_json.tsv" || true
find "$run_work/out" -name '*.cov.json' -type f -delete || true

echo "$OUT"
