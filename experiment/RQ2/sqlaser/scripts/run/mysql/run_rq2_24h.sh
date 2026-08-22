#!/usr/bin/env bash
set -euo pipefail

round=${1:?usage: run_rq2_24h.sh r1|r2|r3|r4|r5}
case "$round" in
  r1) core=${SQLASER_CORE_R1:-29} ;;
  r2) core=${SQLASER_CORE_R2:-27} ;;
  r3) core=${SQLASER_CORE_R3:-32} ;;
  r4) core=${SQLASER_CORE_R4:-38} ;;
  r5) core=${SQLASER_CORE_R5:-42} ;;
  *) echo "unknown round: $round" >&2; exit 2 ;;
esac

base=/root/SQLeek/experiment/RQ2/sqlaser/results/mysql827/sqlaser_prototype/formal_24h
runner=/root/SQLeek/experiment/RQ2/sqlaser/scripts/run_mysql827_sqlaser_run.sh
collector=/root/SQLeek/experiment/RQ2/sqlaser/scripts/collect_formal_run.sh
image=${SQLASER_IMAGE:-sqlaser_mysql827_prototype:latest}
seconds=${SQLASER_24H_SECONDS:-86400}
oracle=${SQLASER_ORACLE:-NOREC}
target=/home/mysql/fuzzing/fuzz_root/sqlaser/mysql_target_chains.tsv
ts=$(date +%Y%m%d_%H%M%S)
out=$base/${round}_${ts}

if ! taskset -c "$core" true >/dev/null 2>&1; then
  echo "CPU core $core is not in the current allowed CPU set" >&2
  exit 3
fi

mkdir -p "$out"
chmod 777 "$out"
image_id=$(docker image inspect -f '{{.Id}}' "$image")
avail_kb=$(df -Pk "$base" | awk 'NR==2 {print $4}')
start_time=$(date -Is)
cutoff_time=$(date -d "+${seconds} seconds" -Is)

cat > "$out/formal_launcher_manifest.json" <<JSON
{
  "formal_run": true,
  "round": "$round",
  "cpu_core": $core,
  "image": "$image",
  "image_id": "$image_id",
  "seconds": $seconds,
  "oracle": "$oracle",
  "output": "$out",
  "start_time": "$start_time",
  "cutoff_time": "$cutoff_time",
  "available_kb_before_start": $avail_kb,
  "independent_queue": true,
  "uses_sqleek_seed_corpus": false,
  "reuses_smoke_or_ab_queue": false,
  "fuzzing_dbms_version": "MySQL 8.0.27",
  "fuzzing_dbms_commit": "3290a66c89eb1625a7058e0ef732432b6952b435",
  "fuzzing_instrumentation": "SQLRight/SQLaser instrumentation",
  "sqlright_commit": "9457f0311b70562a3423ee86ac7e2ebdaaa6664b",
  "sqlaser_commit": "7b1decf9f1aac33e1eb55c8e5bc6cd683325db87",
  "distance_type": "sql_structure_proxy",
  "preflight_note": "Started after SQLaser 1h stability at explicit user request; disabled/missing-target controls and 1h A/B remain separate evidence runs."
}
JSON

printf '%s\n' "available_kb=$avail_kb" "start_time=$start_time" "cutoff_time=$cutoff_time" > "$out/disk_preflight.txt"
printf '%s\n' "runner=$runner" "image=$image" "image_id=$image_id" "round=$round" "core=$core" "seconds=$seconds" > "$out/launch_command.txt"

nohup "$runner" "$image" "$out" sqlaser "$seconds" "$core" "$oracle" "$target" > "$out/launcher.log" 2>&1 &
echo $! > "$out/launcher.pid"
nohup "$collector" "$out" > "$out/collector_launcher.log" 2>&1 &
echo $! > "$out/collector.pid"
echo "$out"
