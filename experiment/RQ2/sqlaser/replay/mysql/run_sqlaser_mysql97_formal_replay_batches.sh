#!/usr/bin/env bash
set -euo pipefail

ROOT=${ROOT:-/root/SQLeek}
R=$ROOT/experiment/RQ2/replay
S=$ROOT/experiment/RQ2/sqlaser
IMAGE=${IMAGE:-griffin_mysql_clean_llvmcov:latest}
CHECKPOINTS=${CHECKPOINTS:-0,60,180,300,480,600,720,900,1200,1440}
SEED_TIMEOUT=${SEED_TIMEOUT:-30}
RESTART_EVERY=${RESTART_EVERY:-200}
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=${OUT:-$R/output/sqlaser_mysql827_formal_mysql97_replay_$TS}
WORK=$OUT/work
BUCKET=$OUT/bucketed_mysql
DATA=$OUT/data
TARGET_REGIONS=$OUT/target_regions.csv
LOG=$OUT/driver.log

declare -A RUN_DIRS=(
  [r1]="$S/results/mysql827/sqlaser_prototype/formal_24h/r1_20260712_110033"
  [r2]="$S/results/mysql827/sqlaser_prototype/formal_24h/r2_20260712_110856"
  [r3]="$S/results/mysql827/sqlaser_prototype/formal_24h/r3_20260712_110856"
  [r4]="$S/results/mysql827/sqlaser_prototype/formal_24h/r4_20260712_110856"
  [r5]="$S/results/mysql827/sqlaser_prototype/formal_24h/r5_20260712_110857"
)

mkdir -p "$OUT" "$WORK" "$BUCKET" "$DATA"
exec > >(tee -a "$LOG") 2>&1

echo "start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "out=$OUT"
echo "image=$IMAGE"
echo "checkpoints=$CHECKPOINTS"
echo "seed_timeout=$SEED_TIMEOUT"
echo "restart_every=$RESTART_EVERY"
echo "batch_order=r1+r2,r3+r4,r5"
echo "version_override=mysql_9.7.0_llvm_source_coverage_user_requested"

docker image inspect "$IMAGE" >/dev/null
python3 "$R/build_target_regions.py" --dbms mysql --out "$TARGET_REGIONS"

prepare_queue() {
  local repeat="$1"
  local run_dir="${RUN_DIRS[$repeat]}"
  local meta="$run_dir/checkpoint_replay/first_seen_metadata.tsv"
  local qout="$WORK/$repeat/queue_time_named"
  local manifest="$WORK/$repeat/selected_queue.tsv"
  mkdir -p "$qout"
  test -f "$meta"
  python3 - "$meta" "$qout" "$manifest" <<'PY'
from __future__ import annotations

import csv
import os
import re
import shutil
import sys
from pathlib import Path

meta = Path(sys.argv[1])
out = Path(sys.argv[2])
manifest = Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
rows = []
with meta.open(newline="", encoding="utf-8", errors="replace") as fp:
    reader = csv.DictReader(fp, delimiter="\t")
    for row in reader:
        src = Path(row["source_queue_file"])
        if not src.is_file():
            raise SystemExit(f"missing source queue file: {src}")
        elapsed = int(float(row["first_seen_offset_ms"]))
        materialized = row.get("materialized_id") or "999999"
        m = re.search(r"\d+", materialized)
        seq = int(m.group(0)) if m else 999999
        rows.append((elapsed, seq, row["source_name"], src))
rows.sort(key=lambda x: (x[0], x[1], x[2]))
with manifest.open("w", encoding="utf-8", newline="") as fp:
    fp.write("elapsed_ms\tseq\tsource_name\tsource_queue_file\treplay_path\n")
    for idx, (elapsed, seq, name, src) in enumerate(rows, start=1):
        safe = name.replace("/", "_")
        dst = out / f"time:{elapsed},seq:{idx:06d},{safe}"
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        fp.write(f"{elapsed}\t{seq}\t{name}\t{src}\t{dst}\n")
print(f"prepared_queue_files={len(rows)}")
print(f"first_time_ms={rows[0][0] if rows else ''}")
print(f"last_time_ms={rows[-1][0] if rows else ''}")
PY
}

run_repeat() {
  local repeat="$1"
  local repeat_id="${repeat#r}"
  local qdir="$WORK/$repeat/queue_time_named"
  local rout="$BUCKET/$repeat"
  local run_id="sqlaser_mysql_r${repeat_id}"
  mkdir -p "$rout"
  echo "repeat_start repeat=$repeat utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  docker run --rm --privileged -m 30G --shm-size=4G \
    --name "sqlaser_mysql97_cov_${repeat}_${TS}" \
    -e "RQ2_PROFILE_RUN_ID=${run_id}_profiles" \
    -v "$R:/rq2_scripts:ro" \
    -v "$qdir:/rq2_queue:ro" \
    -v "$rout:/rq2_out" \
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
    run_repeat "$repeat" > "$OUT/${repeat}.replay.log" 2>&1 &
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

for repeat in r1 r2 r3 r4 r5; do
  run_dir="${RUN_DIRS[$repeat]}"
  test -d "$run_dir"
  test -f "$run_dir/checkpoint_replay/checkpoint_manifest.json"
  prepare_queue "$repeat" | sed "s/^/prepare_${repeat}: /"
done

cat > "$OUT/replay_manifest.tsv" <<EOF_MANIFEST
field	value
tool	SQLaser
dbms	mysql
fuzz_target	mysql827
replay_mysql_version	9.7.0
replay_mode	LLVM source coverage
version_override	user requested all replay containers use MySQL 9.7.0
image	$IMAGE
checkpoints_min	$CHECKPOINTS
batch_order	r1+r2,r3+r4,r5
input_r1	${RUN_DIRS[r1]}
input_r2	${RUN_DIRS[r2]}
input_r3	${RUN_DIRS[r3]}
input_r4	${RUN_DIRS[r4]}
input_r5	${RUN_DIRS[r5]}
EOF_MANIFEST
docker image inspect "$IMAGE" --format 'image_id	{{.Id}}' >> "$OUT/replay_manifest.tsv"

run_batch "batch1_r1_r2" r1 r2
run_batch "batch2_r3_r4" r3 r4
run_batch "batch3_r5" r5

python3 "$ROOT/experiment/RQ2/scripts/resummarize_mysql_from_profdata.py" \
  --out-dir "$DATA" \
  --target-regions "$TARGET_REGIONS" \
  --profdata-root "$BUCKET" \
  --tool SQLaser \
  --repeats 1,2,3,4,5 \
  --image "$IMAGE" 2>&1 | tee "$OUT/resummarize_sqlaser_mysql97.log"

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
driver_log=$LOG
batch_logs=$OUT/r1.replay.log,$OUT/r2.replay.log,$OUT/r3.replay.log,$OUT/r4.replay.log,$OUT/r5.replay.log
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
echo "end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
