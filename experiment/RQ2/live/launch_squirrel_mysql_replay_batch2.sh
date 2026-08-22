#!/usr/bin/env bash
set -euo pipefail

BASE=/root/SQLeek/experiment/RQ2
PARENT="$BASE/replay/output/squirrel_mysql_bucketed_replay_batch2_$(date -u +%Y%m%d_%H%M%S)"
WORK="$PARENT/work"
OUT="$PARENT/bucketed_squirrel_mysql"
REAL="$PARENT/real_squirrel_mysql"
LOG="$PARENT/launch.log"
mkdir -p "$WORK" "$OUT" "$REAL"

IMAGE=griffin_mysql_clean_llvmcov:latest
RUNNER=/root/SQLeek/experiment/RQ2/replay/mysql_clean_bucketed_replay_runner.sh
CHECKPOINTS=60,180,300,480,600,720,900,1200,1440
SEED_TIMEOUT=60
RESTART_EVERY=100
PORT=8888

declare -A SRC
SRC[1]=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502/containers/rq2_squirrel_mysql_r1_20260615_081534/cutoff_24h_default.tar.gz
SRC[2]=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502/containers/rq2_squirrel_mysql_r2_20260615_more5b/cutoff_24h_default.tar.gz
SRC[3]=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502/containers/rq2_squirrel_mysql_r3_20260615_more5b/cutoff_24h_default.tar.gz
SRC[4]=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502/containers/rq2_squirrel_mysql_r4_20260615_more5b/cutoff_24h_default.tar.gz
SRC[5]=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502/containers/rq2_squirrel_mysql_r5_20260615_more5b/cutoff_24h_default.tar.gz

for r in 1 2 3 4 5; do
  [[ -f "${SRC[$r]}" ]] || { echo "missing source tar: ${SRC[$r]}" >&2; exit 2; }
  mkdir -p "$WORK/r$r/queue"
  tar -xzf "${SRC[$r]}" -C "$WORK/r$r" queue fuzzer_stats
done

launch_one() {
  local r=$1
  local name="rq2_squirrel_mysql_replay_r${r}_$(basename "$PARENT")"
  local out="$OUT/r$r"
  local cid
  mkdir -p "$out"
  cid=$(docker run --privileged -d \
    --cpus=4 \
    --memory=36g \
    --shm-size=8g \
    -v /root/SQLeek/experiment/RQ2/replay:/rq2_scripts:ro \
    -v "$WORK/r$r/queue":/rq2_queue:ro \
    -v "$out":/rq2_out \
    --name "$name" \
    --entrypoint /bin/bash \
    "$IMAGE" \
    /rq2_scripts/mysql_clean_bucketed_replay_runner.sh \
      --queue-dir /rq2_queue \
      --out-dir /rq2_out \
      --run-id "squirrel_mysql_r${r}" \
      --checkpoints-min "$CHECKPOINTS" \
      --seed-timeout "$SEED_TIMEOUT" \
      --restart-every "$RESTART_EVERY" \
      --port "$PORT")
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launch r${r} cid=$cid name=$name" >> "$LOG"
  echo "$name"
}

wait_batch() {
  local containers=("$@")
  local done=0
  while [[ "$done" -eq 0 ]]; do
    done=1
    for c in "${containers[@]}"; do
      state=$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$c")
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $c $state" >> "$LOG"
      status=${state%% *}
      code=${state##* }
      if [[ "$status" == "running" ]]; then
        done=0
      elif [[ "$code" != "0" ]]; then
        echo "batch failure: $c exited with $code" >&2
        exit 20
      fi
    done
    [[ "$done" -eq 1 ]] || sleep 60
  done
  for c in "${containers[@]}"; do
    docker rm "$c" >/dev/null
  done
}

batch1=()
batch1+=("$(launch_one 1)")
batch1+=("$(launch_one 2)")
wait_batch "${batch1[@]}"

batch2=()
batch2+=("$(launch_one 3)")
batch2+=("$(launch_one 4)")
wait_batch "${batch2[@]}"

batch3=()
batch3+=("$(launch_one 5)")
wait_batch "${batch3[@]}"

cd "$BASE"
python3 replay/build_target_regions.py --dbms mysql --out "$REAL/target_regions.csv"
python3 scripts/resummarize_mysql_from_profdata.py \
  --out-dir "$REAL" \
  --target-regions "$REAL/target_regions.csv" \
  --profdata-root "$OUT" \
  --tool SQUIRREL
python3 scripts/apply_real_squirrel_pg_result.py \
  --merge-into-existing \
  --tool SQUIRREL \
  --real-data "$REAL"
python3 scripts/plot_target_branch_region_over_time.py

echo "$PARENT"
