#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek
REPLAY_DIR="$ROOT/experiment/RQ2/replay"
RQ2_DIR="$ROOT/experiment/RQ2"
QUEUE_ROOT="$RQ2_DIR/dynsql/output/mysql8022_5x24h"
IMAGE=griffin_mysql_clean_llvmcov
CHECKPOINTS=0,60,180,300,480,600,720,900,1200,1440
STAMP=$(date -u +%Y%m%d_%H%M%S)
OUT="$REPLAY_DIR/output/dynsql_mysql8022_r1_r2_replay_$STAMP"
TARGET_SOURCE="$REPLAY_DIR/output/sqleek_mysql_bucketed_replay_binary_mode_fresh_isolation_20260703_1659/target_regions.csv"

mkdir -p "$OUT/logs"
cp "$TARGET_SOURCE" "$OUT/target_regions.csv"
printf '%s\n' "$OUT" > /tmp/rq2_dynsql_mysql8022_r1_r2_latest.path

for repeat in 1 2; do
  queue="$QUEUE_ROOT/r${repeat}/queue"
  run_out="$OUT/r${repeat}"
  run_id="dynsql_mysql_r${repeat}"
  test -d "$queue"
  mkdir -p "$run_out"
  docker run --rm --privileged -m 40G --shm-size=4G \
    --name "rq2_dynsql_mysql_r${repeat}_${STAMP}" \
    -v "$REPLAY_DIR:/rq2_scripts:ro" \
    -v "$queue:/rq2_queue:ro" \
    -v "$run_out:/rq2_out" \
    --entrypoint /bin/bash "$IMAGE" \
    /rq2_scripts/mysql_clean_bucketed_replay_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "$run_id" \
      --checkpoints-min "$CHECKPOINTS" \
      --max-seeds 0 \
      --seed-timeout 60 \
      --restart-every 0 \
    > "$OUT/logs/${run_id}.log" 2>&1
done

python3 "$RQ2_DIR/scripts/resummarize_mysql_from_profdata.py" \
  --out-dir "$OUT/data" \
  --target-regions "$OUT/target_regions.csv" \
  --profdata-root "$OUT" \
  --tool DynSQL \
  --repeats 1,2 \
  --image "$IMAGE" \
  > "$OUT/logs/resummarize.log" 2>&1

python3 - "$OUT/data/coverage_timeseries.csv" "$OUT/data/coverage_timeseries_stats.csv" <<'PY'
import csv
import math
import statistics
import sys
from collections import defaultdict

source, target = sys.argv[1:]
with open(source, newline="") as f:
    rows = list(csv.DictReader(f))
groups = defaultdict(list)
for row in rows:
    groups[(row["tool"], row["dbms"], int(row["elapsed_min"]))].append(row)
fields = [
    "tool", "dbms", "elapsed_min", "n",
    "mean_target_region_branch_coverage", "std_target_region_branch_coverage",
    "se_target_region_branch_coverage", "ci95_target_region_branch_coverage",
    "mean_risk_branches_hit", "std_risk_branches_hit",
    "mean_risk_targets_hit", "std_risk_targets_hit",
    "mean_global_branch_coverage", "std_global_branch_coverage",
    "se_global_branch_coverage", "ci95_global_branch_coverage",
]
with open(target, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for (tool, dbms, elapsed), values in sorted(groups.items(), key=lambda item: item[0][2]):
        def stats(name):
            nums = [float(row[name]) for row in values]
            mean = statistics.mean(nums)
            std = statistics.stdev(nums) if len(nums) > 1 else 0.0
            se = std / math.sqrt(len(nums))
            return mean, std, se, 1.96 * se
        tc = stats("target_region_branch_coverage")
        rb = stats("risk_branches_hit")
        rt = stats("risk_targets_hit")
        gc = stats("global_branch_coverage")
        writer.writerow({
            "tool": tool, "dbms": dbms, "elapsed_min": elapsed, "n": len(values),
            "mean_target_region_branch_coverage": tc[0],
            "std_target_region_branch_coverage": tc[1],
            "se_target_region_branch_coverage": tc[2],
            "ci95_target_region_branch_coverage": tc[3],
            "mean_risk_branches_hit": rb[0], "std_risk_branches_hit": rb[1],
            "mean_risk_targets_hit": rt[0], "std_risk_targets_hit": rt[1],
            "mean_global_branch_coverage": gc[0], "std_global_branch_coverage": gc[1],
            "se_global_branch_coverage": gc[2], "ci95_global_branch_coverage": gc[3],
        })
PY

find "$OUT" -type f \( -name '*.cov.json' -o -name '*.target.lcov' -o -name '*.summary.json' \) -delete
printf 'output=%s\n' "$OUT"
