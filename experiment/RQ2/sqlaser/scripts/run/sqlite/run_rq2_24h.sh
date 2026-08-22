#!/usr/bin/env bash
set -euo pipefail
round=${1:?usage: run_rq2_24h.sh r1|r2|r3|r4|r5}
case "$round" in
  r1) core=${SQLASER_CORE:-6} ;;
  r2) core=${SQLASER_CORE:-10} ;;
  r3) core=${SQLASER_CORE:-17} ;;
  r4) core=${SQLASER_CORE:-20} ;;
  r5) core=${SQLASER_CORE:-23} ;;
  *) echo "unknown round: $round" >&2; exit 2 ;;
esac
base=/root/SQLeek/experiment/RQ2/sqlaser/results/sqlite354/formal_24h
helper=/root/SQLeek/experiment/RQ2/sqlaser/run_sqlaser_container_once.sh
collector=/root/SQLeek/experiment/RQ2/sqlaser/scripts/collect_formal_run.sh
image=sqlaser_sqlite354_prototype:latest
seconds=86400
oracle=NOREC
ts=$(date +%Y%m%d_%H%M%S)
out=$base/${round}_${ts}
mkdir -p "$out"
chmod 777 "$out"
echo "$out" > "$base/latest_${round}"
cat > "$out/formal_launcher_manifest.json" <<JSON
{
  "round": "$round",
  "core": "$core",
  "image": "$image",
  "seconds": $seconds,
  "oracle": "$oracle",
  "output": "$out",
  "start_launcher_time": "$(date -Is)",
  "independent_queue": true,
  "uses_sqleek_seed_corpus": false,
  "reuses_smoke_or_ab_queue": false,
  "fuzzing_dbms_version": "SQLite 3.54.0",
  "fuzzing_instrumentation": "SQLRight/SQLaser instrumentation",
  "replay_dbms_version": "SQLite 3.54.0",
  "replay_instrumentation": "SQLeek unified LLVM coverage"
}
JSON
nohup "$helper" "$image" "$out" "$core" "$seconds" "$oracle" sqlaser >/tmp/sqlaser_${round}_24h_${ts}.launch.log 2>&1 &
echo $! > "$out/launcher.pid"
nohup "$collector" "$out" >"$out/collector_launcher.log" 2>&1 &
echo $! > "$out/collector.pid"
echo "$out"
