#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
IMAGE=${RQ2_SQLITE_IMAGE:-griffin_sqlite_llvmcov}
BINARY=${RQ2_SQLITE_BINARY:-/root/bld_llvmcov/sqlite3}
SQLITE_AMALGAMATION_IN_IMAGE=${RQ2_SQLITE_AMALGAMATION_IN_IMAGE:-/root/bld_llvmcov/sqlite3.c}
RUN_ID=${RUN_ID:-sqlite_aflgo_r1_live}
REPEAT_ID=${REPEAT_ID:-1}
TOOL=${TOOL:-AFLGo}
DBMS=${DBMS:-sqlite}
VERSION=${VERSION:-3.53.2}
QUEUE_DIR=${QUEUE_DIR:-/root/SQLeek/experiment/RQ2/aflgo/sqlite/output_rq2_24h/sqlite_aflgo_r1_20260707_063357/sqlite_aflgo_r1/queue}
CONTAINER_ID=${CONTAINER_ID:-sqlite_aflgo_r1_20260707_063357}
CHECKPOINTS_MIN=${CHECKPOINTS_MIN:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${SEED_TIMEOUT:-10}
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-30G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-8G}
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=${OUT:-$SCRIPT_DIR/output/aflgo_sqlite_full_replay_${TS}}
WORK=$OUT/work
QUEUE_NAMED=$WORK/queue_time_named
DATA=$OUT/data
LOG=$OUT/driver.log

mkdir -p "$OUT" "$WORK" "$QUEUE_NAMED" "$DATA" "$OUT/source_map"
exec > >(tee -a "$LOG") 2>&1

echo "out=$OUT"
echo "queue_dir=$QUEUE_DIR"
echo "image=$IMAGE"
echo "binary=$BINARY"
echo "checkpoints_min=$CHECKPOINTS_MIN"
echo "seed_timeout=$SEED_TIMEOUT"

test -d "$QUEUE_DIR"
docker image inspect "$IMAGE" >/dev/null

docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "test -x '$BINARY' && test -f '$SQLITE_AMALGAMATION_IN_IMAGE' && command -v llvm-profdata-12 && command -v llvm-cov-12"
docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "cat '$SQLITE_AMALGAMATION_IN_IMAGE'" > "$OUT/source_map/sqlite3_llvmcov.c"

python3 "$SCRIPT_DIR/build_target_regions.py" --dbms sqlite --out "$OUT/target_regions.csv"

python3 - "$QUEUE_DIR" "$QUEUE_NAMED" "$OUT/selected_queue.tsv" <<'PY'
from __future__ import annotations
import os
import re
import shutil
import sys
from pathlib import Path

queue = Path(sys.argv[1])
out = Path(sys.argv[2])
manifest = Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
rows = []
bad = []
for p in queue.iterdir():
    if not p.is_file():
        continue
    name = p.name
    if name == "README.txt" or name.startswith("."):
        continue
    t = None
    m = re.search(r"(?:^|,)time:(\d+)(?:,|$)", name)
    if m:
        t = int(m.group(1))
    elif ",orig:" in name:
        t = 0
    else:
        m = re.match(r"id:\d+,(\d+),", name)
        if m:
            t = int(m.group(1))
    if t is None:
        bad.append(name)
        continue
    m_id = re.match(r"id:(\d+)", name)
    sid = int(m_id.group(1)) if m_id else 10**12
    rows.append((t, sid, name, p))
rows.sort(key=lambda r: (r[0], r[1], r[2]))
if bad:
    raise SystemExit(f"failed to parse AFL queue times for {len(bad)} seeds, examples={bad[:10]}")
with manifest.open("w", encoding="utf-8") as fp:
    fp.write("elapsed_ms\toriginal_name\toriginal_path\treplay_path\n")
    for idx, (t, _sid, name, src) in enumerate(rows, start=1):
        dst = out / f"time:{t},seq:{idx:06d},{name}"
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        fp.write(f"{t}\t{name}\t{src}\t{dst}\n")
print(f"selected={len(rows)}")
if rows:
    print(f"min_time_ms={rows[0][0]}")
    print(f"max_time_ms={rows[-1][0]}")
PY

CHECKPOINTS_MS=$(python3 - "$CHECKPOINTS_MIN" <<'PY'
import sys
mins=[int(x.strip()) for x in sys.argv[1].split(',') if x.strip()]
print(','.join(str(m*60000) for m in mins))
PY
)
START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOST_PREFIX=$WORK/${RUN_ID}
CONTAINER_PREFIX=/rq2_out/${RUN_ID}
DOCKER_NAME=rq2_aflgo_sqlite_replay_${TS}_$$

echo "checkpoints_ms=$CHECKPOINTS_MS"
echo "docker_name=$DOCKER_NAME"
set +e
docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
  -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
  -v "$SCRIPT_DIR":/rq2_scripts:ro \
  -v "$QUEUE_NAMED":/rq2_queue:ro \
  -v "$WORK":/rq2_out \
  --name "$DOCKER_NAME" --entrypoint /bin/bash "$IMAGE" \
  /rq2_scripts/container_replay_llvm_bucketed.sh \
    --dbms sqlite \
    --binary "$BINARY" \
    --checkpoints-ms "$CHECKPOINTS_MS" \
    --seed-timeout "$SEED_TIMEOUT" \
    --out-prefix "$CONTAINER_PREFIX"
rc=$?
set -e
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "docker_rc=$rc"

INDEX=$OUT/replay_index.tsv
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$INDEX"
IFS=',' read -r -a CPS <<< "$CHECKPOINTS_MIN"
for cp in "${CPS[@]}"; do
  cp=${cp//[[:space:]]/}
  [[ -n "$cp" ]] || continue
  cov_json="${HOST_PREFIX}_t${cp}.cov.json"
  report_txt="${HOST_PREFIX}_t${cp}.report.txt"
  meta="${HOST_PREFIX}_t${cp}.meta.tsv"
  seed_count=0
  if [[ -f "$meta" ]]; then
    seed_count=$(awk -F'\t' '$1=="seed_count"{print $2}' "$meta")
  fi
  status=complete
  message=
  if [[ "$rc" -ne 0 ]]; then
    status=failed
    message="docker replay failed rc=$rc"
  elif [[ ! -f "$cov_json" ]]; then
    status=failed
    message="missing cov_json for checkpoint $cp"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$RUN_ID" "$TOOL" "$DBMS" "$REPEAT_ID" "$cp" "$cov_json" "$report_txt" "$status" "$message" \
    "$IMAGE" "$BINARY" "$seed_count" "$QUEUE_DIR" "$IMAGE" "$CONTAINER_ID" "$VERSION" "$START_TIME" "$END_TIME" >> "$INDEX"
done

python3 "$SCRIPT_DIR/summarize_llvm_cov.py" \
  --target-regions "$OUT/target_regions.csv" \
  --replay-index "$INDEX" \
  --sqlite-amalgamation "$OUT/source_map/sqlite3_llvmcov.c" \
  --tool "$TOOL" \
  --out "$DATA" 2>&1 | tee "$OUT/summarize.log"

python3 - "$DATA" <<'PY'
from __future__ import annotations
import csv, math, pathlib, statistics, sys
from collections import defaultdict

data = pathlib.Path(sys.argv[1])
ts = list(csv.DictReader((data/'coverage_timeseries.csv').open(newline='')))
fields = ['tool','dbms','elapsed_min','n','mean_target_region_branch_coverage','std_target_region_branch_coverage','se_target_region_branch_coverage','ci95_target_region_branch_coverage','mean_risk_branches_hit','std_risk_branches_hit','mean_risk_targets_hit','std_risk_targets_hit','mean_global_branch_coverage','std_global_branch_coverage','se_global_branch_coverage','ci95_global_branch_coverage']
groups=defaultdict(list)
for r in ts:
    groups[(r['tool'],r['dbms'],r['elapsed_min'])].append(r)
with (data/'coverage_timeseries_stats.csv').open('w', newline='') as fp:
    w=csv.DictWriter(fp, fieldnames=fields); w.writeheader()
    for (tool, dbms, elapsed), rows in sorted(groups.items(), key=lambda x:(x[0][0],x[0][1],int(x[0][2]))):
        def vals(k): return [float(r[k]) for r in rows]
        def mean(v): return sum(v)/len(v) if v else 0.0
        def sd(v): return statistics.stdev(v) if len(v)>1 else 0.0
        cov=vals('target_region_branch_coverage'); glob=vals('global_branch_coverage')
        rb=vals('risk_branches_hit'); rt=vals('risk_targets_hit')
        n=len(rows); cov_sd=sd(cov); glob_sd=sd(glob)
        cov_se=cov_sd/math.sqrt(n) if n else 0.0; glob_se=glob_sd/math.sqrt(n) if n else 0.0
        w.writerow({'tool':tool,'dbms':dbms,'elapsed_min':elapsed,'n':n,'mean_target_region_branch_coverage':mean(cov),'std_target_region_branch_coverage':cov_sd,'se_target_region_branch_coverage':cov_se,'ci95_target_region_branch_coverage':1.96*cov_se,'mean_risk_branches_hit':mean(rb),'std_risk_branches_hit':sd(rb),'mean_risk_targets_hit':mean(rt),'std_risk_targets_hit':sd(rt),'mean_global_branch_coverage':mean(glob),'std_global_branch_coverage':glob_sd,'se_global_branch_coverage':glob_se,'ci95_global_branch_coverage':1.96*glob_se})
PY

cat > "$OUT/REPLAY_SUMMARY.txt" <<EOF_SUMMARY
out=$OUT
queue_dir=$QUEUE_DIR
queue_files=$(find "$QUEUE_DIR" -maxdepth 1 -type f ! -name README.txt | wc -l | tr -d ' ')
selected_queue_files=$(find "$QUEUE_NAMED" -maxdepth 1 -type f | wc -l | tr -d ' ')
docker_rc=$rc
replay_index=$INDEX
data_dir=$DATA
coverage_summary=$DATA/coverage_summary.csv
coverage_timeseries=$DATA/coverage_timeseries.csv
coverage_timeseries_stats=$DATA/coverage_timeseries_stats.csv
component_heatmap=$DATA/component_heatmap.csv
target_region_hits=$DATA/target_region_hits.csv
target_branch_hits=$DATA/target_branch_hits.csv
EOF_SUMMARY
cat "$OUT/REPLAY_SUMMARY.txt"
exit "$rc"
