#!/usr/bin/env bash
set -euo pipefail

base=/root/SQLeek/experiment/RQ2/sqlaser/results/mysql827/sqlaser_prototype/formal_24h
log=$base/disk_monitor.tsv
printf '%s\n' 'timestamp\tavail_kb\troot_use\tactive_mysql_containers\tformal_tree_kb\tdocker_dir_kb' > "$log"
while :; do
  timestamp=$(date -Is)
  avail_kb=$(df -Pk / | awk 'NR==2 {print $4}')
  root_use=$(df -P / | awk 'NR==2 {print $5}')
  active=$(docker ps --filter ancestor=sqlaser_mysql827_prototype:latest --format '{{.Names}}' | paste -sd, -)
  formal_kb=$(du -sk "$base" 2>/dev/null | awk '{print $1}')
  docker_kb=$(du -sk /var/lib/docker 2>/dev/null | awk '{print $1}')
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$timestamp" "$avail_kb" "$root_use" "${active:-none}" "$formal_kb" "$docker_kb" >> "$log"
  sleep 600
done
