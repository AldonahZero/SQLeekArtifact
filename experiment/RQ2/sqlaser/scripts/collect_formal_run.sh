#!/usr/bin/env bash
set -euo pipefail
run_dir=${1:?usage: collect_formal_run.sh RUN_DIR}
collector=/root/SQLeek/experiment/RQ2/sqlaser/scripts/collect_sqlaser_run.py
mkdir -p "$run_dir"
exec > >(tee -a "$run_dir/collector.log") 2>&1
echo "collector_start=$(date -Is) run=$run_dir"
python3 "$collector" --run "$run_dir" --wait
echo "collector_end=$(date -Is)"
