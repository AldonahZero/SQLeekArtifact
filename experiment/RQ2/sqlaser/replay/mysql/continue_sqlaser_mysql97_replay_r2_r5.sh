#!/usr/bin/env bash
set -euo pipefail

ROOT=${ROOT:-/root/SQLeek}
R=$ROOT/experiment/RQ2/replay
IMAGE=${IMAGE:-griffin_mysql_clean_llvmcov:latest}
CHECKPOINTS=${CHECKPOINTS:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${SEED_TIMEOUT:-30}
RESTART_EVERY=${RESTART_EVERY:-0}
OUT=${OUT:-$(cat "$ROOT/experiment/RQ2/sqlaser/replay/mysql/latest_sqlaser_mysql97_replay.out")}
WORK=$OUT/work
BUCKET=$OUT/bucketed_mysql
DATA=$OUT/data
TARGET_REGIONS=$OUT/target_regions.csv
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
LOG=$OUT/continue_r2_r5_$TS.log

mkdir -p "$OUT" "$BUCKET" "$DATA"
exec > >(tee -a "$LOG") 2>&1

echo "continue_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "out=$OUT"
echo "image=$IMAGE"
echo "checkpoints=$CHECKPOINTS"
echo "seed_timeout=$SEED_TIMEOUT"
echo "restart_every=$RESTART_EVERY"
echo "continue_repeats=r2,r3,r4,r5"

test -d "$WORK/r1/queue_time_named"
test -d "$WORK/r2/queue_time_named"
test -d "$WORK/r3/queue_time_named"
test -d "$WORK/r4/queue_time_named"
test -d "$WORK/r5/queue_time_named"
test -s "$TARGET_REGIONS"
docker image inspect "$IMAGE" >/dev/null

for repeat in r2 r3 r4 r5; do
  rm -rf "$BUCKET/$repeat"
  mkdir -p "$BUCKET/$repeat"
done
rm -rf "$DATA"
mkdir -p "$DATA"

run_repeat() {
  local repeat="$1"
  local repeat_id="${repeat#r}"
  local run_id="sqlaser_mysql_r${repeat_id}"
  echo "repeat_start repeat=$repeat utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  docker run --rm --privileged -m 30G --shm-size=4G \
    --name "sqlaser_mysql97_cov_${repeat}_${TS}" \
    -e "RQ2_PROFILE_RUN_ID=${run_id}_profiles" \
    -v "$R:/rq2_scripts:ro" \
    -v "$WORK/$repeat/queue_time_named:/rq2_queue:ro" \
    -v "$BUCKET/$repeat:/rq2_out" \
    --entrypoint /bin/bash "$IMAGE" \
    /rq2_scripts/mysql_clean_bucketed_replay_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "$run_id" \
      --checkpoints-min "$CHECKPOINTS" \
      --seed-timeout "$SEED_TIMEOUT" \
      --restart-every "$RESTART_EVERY"
  echo "repeat_done repeat=$repeat utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

run_batch() {
  local name="$1"
  shift
  echo "batch_start name=$name repeats=$* utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local pids=()
  local repeats=()
  for repeat in "$@"; do
    run_repeat "$repeat" > "$OUT/${repeat}.continue.log" 2>&1 &
    pids+=("$!")
    repeats+=("$repeat")
  done
  local failed=0
  for idx in "${!pids[@]}"; do
    set +e
    wait "${pids[$idx]}"
    local rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      echo "batch_repeat_failed name=$name repeat=${repeats[$idx]} rc=$rc"
      failed=1
    fi
  done
  if [[ "$failed" -ne 0 ]]; then
    echo "batch_failed name=$name utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 1
  fi
  echo "batch_done name=$name utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

run_batch "continue_r2" r2
run_batch "continue_r3_r4" r3 r4
run_batch "continue_r5" r5

python3 "$ROOT/experiment/RQ2/scripts/resummarize_mysql_from_profdata.py" \
  --out-dir "$DATA" \
  --target-regions "$TARGET_REGIONS" \
  --profdata-root "$BUCKET" \
  --tool SQLaser \
  --repeats 1,2,3,4,5 \
  --image "$IMAGE" 2>&1 | tee "$OUT/resummarize_sqlaser_mysql97_continue_$TS.log"

python3 - "$DATA" <<'PY'
from __future__ import annotations

import csv
import math
import pathlib
import statistics
import sys
from collections import defaultdict

real = pathlib.Path(sys.argv[1])
rows = list(csv.DictReader((real / "coverage_timeseries.csv").open(newline="")))
fields = [
    "tool",
    "dbms",
    "elapsed_min",
    "n",
    "mean_target_region_branch_coverage",
    "std_target_region_branch_coverage",
    "se_target_region_branch_coverage",
    "ci95_target_region_branch_coverage",
    "mean_risk_branches_hit",
    "std_risk_branches_hit",
    "mean_risk_targets_hit",
    "std_risk_targets_hit",
    "mean_global_branch_coverage",
    "std_global_branch_coverage",
    "se_global_branch_coverage",
    "ci95_global_branch_coverage",
]
grouped = defaultdict(list)
for row in rows:
    grouped[(row["tool"], row["dbms"], row["elapsed_min"])].append(row)

def vals(items, key):
    return [float(item[key]) for item in items]

def mean(values):
    return sum(values) / len(values) if values else 0.0

def stdev(values):
    return statistics.stdev(values) if len(values) > 1 else 0.0

with (real / "coverage_timeseries_stats.csv").open("w", newline="") as fp:
    writer = csv.DictWriter(fp, fieldnames=fields)
    writer.writeheader()
    for (tool, dbms, elapsed), items in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1], int(x[0][2]))):
        n = len(items)
        cov = vals(items, "target_region_branch_coverage")
        glob = vals(items, "global_branch_coverage")
        rb = vals(items, "risk_branches_hit")
        rt = vals(items, "risk_targets_hit")
        cov_sd = stdev(cov)
        glob_sd = stdev(glob)
        cov_se = cov_sd / math.sqrt(n) if n else 0.0
        glob_se = glob_sd / math.sqrt(n) if n else 0.0
        writer.writerow({
            "tool": tool,
            "dbms": dbms,
            "elapsed_min": elapsed,
            "n": n,
            "mean_target_region_branch_coverage": mean(cov),
            "std_target_region_branch_coverage": cov_sd,
            "se_target_region_branch_coverage": cov_se,
            "ci95_target_region_branch_coverage": 1.96 * cov_se,
            "mean_risk_branches_hit": mean(rb),
            "std_risk_branches_hit": stdev(rb),
            "mean_risk_targets_hit": mean(rt),
            "std_risk_targets_hit": stdev(rt),
            "mean_global_branch_coverage": mean(glob),
            "std_global_branch_coverage": glob_sd,
            "se_global_branch_coverage": glob_se,
            "ci95_global_branch_coverage": 1.96 * glob_se,
        })
PY

for f in runs.csv coverage_summary.csv coverage_timeseries.csv coverage_timeseries_stats.csv target_region_hits.csv target_branch_hits.csv component_heatmap.csv component_heatmap_by_run.csv replay_index.tsv target_regions.csv; do
  test -s "$DATA/$f"
done

cat > "$OUT/REPLAY_SUMMARY.txt" <<EOF_SUMMARY
out=$OUT
data=$DATA
bucketed=$BUCKET
target_regions=$TARGET_REGIONS
manifest=$OUT/replay_manifest.tsv
driver_log=$OUT/driver.log
continue_log=$LOG
coverage_summary=$DATA/coverage_summary.csv
coverage_timeseries=$DATA/coverage_timeseries.csv
coverage_timeseries_stats=$DATA/coverage_timeseries_stats.csv
target_region_hits=$DATA/target_region_hits.csv
target_branch_hits=$DATA/target_branch_hits.csv
component_heatmap=$DATA/component_heatmap.csv
component_heatmap_by_run=$DATA/component_heatmap_by_run.csv
replay_index=$DATA/replay_index.tsv
end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF_SUMMARY

cat "$OUT/REPLAY_SUMMARY.txt"
echo "continue_end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
