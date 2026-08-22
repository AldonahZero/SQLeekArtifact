#!/usr/bin/env bash
set -euo pipefail
run_dir=${1:?usage: prepare_sqlite_checkpoint_replay.sh RUN_DIR}
python3 /root/SQLeek/experiment/RQ2/sqlaser/scripts/collect_sqlaser_run.py --run "$run_dir" --prepare-only
