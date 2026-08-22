#!/usr/bin/env bash
set -euo pipefail
base=/root/SQLeek/experiment/RQ2/sqlaser/results/sqlite354/formal_24h
collector=/root/SQLeek/experiment/RQ2/sqlaser/scripts/collect_formal_run.sh
for round in r1 r2 r3 r4 r5; do
  link="$base/latest_$round"
  [[ -f "$link" ]] || continue
  run_dir=$(cat "$link")
  if [[ -f "$run_dir/collector.pid" ]] && kill -0 "$(cat "$run_dir/collector.pid")" 2>/dev/null; then
    echo "$round collector already running pid=$(cat "$run_dir/collector.pid")"
    continue
  fi
  nohup "$collector" "$run_dir" >"$run_dir/collector_launcher.log" 2>&1 &
  echo $! > "$run_dir/collector.pid"
  echo "$round collector_started pid=$! run=$run_dir"
done
