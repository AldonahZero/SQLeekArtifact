#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2/replay
IMAGE=griffin_sqlite_llvmcov
BINARY=/root/bld_llvmcov/sqlite3
RUNNER=$ROOT/sqlaser_sqlite_replay/container_replay_sqlite_leaksafe.sh
CORPUS=/root/SQLeek/experiment/RQ2/sqlaser/results/sqlite354/formal_24h/r1_20260711_174208/checkpoint_replay/checkpoint_0060m/queue
CPU=${REPLAY_CPU:-44}
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=${OUT:-$ROOT/output/sqlaser_sqlite354_r1_checkpoint60_smoke_${TS}}
PREFIX=$OUT/r1_checkpoint60

mkdir -p "$OUT"
seed_count=$(find "$CORPUS" -maxdepth 1 -type f | wc -l | tr -d ' ')
[[ "$seed_count" -gt 0 ]] || { echo "empty corpus: $CORPUS" >&2; exit 2; }
image_id=$(docker image inspect -f '{{.Id}}' "$IMAGE")
disk_before_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat > "$OUT/run_manifest.json" <<JSON
{
  "kind": "r1 checkpoint_60m smoke",
  "corpus": "$CORPUS",
  "seed_count": $seed_count,
  "image": "$IMAGE",
  "image_id": "$image_id",
  "binary": "$BINARY",
  "fuzzing_sqlite_version": "3.54.0",
  "replay_sqlite_version": "3.53.2",
  "version_mismatch_user_override": true,
  "checkpoint_min": 60,
  "seed_timeout_seconds": 10,
  "cpu": $CPU,
  "tmp_profile": "independent 1GiB /tmp tmpfs",
  "tmp_database": "independent 256MiB /rq2_sqlite_tmp tmpfs",
  "minimum_free_kb": 15728640,
  "start_time": "$start_time"
}
JSON

cat > "$OUT/command.txt" <<EOF
docker run --rm --cpuset-cpus $CPU --memory 4g --tmpfs /tmp:rw,nosuid,nodev,size=1g --tmpfs /rq2_sqlite_tmp:rw,nosuid,nodev,size=256m -e ROLLING_MERGE_INTERVAL=50 -e MIN_FREE_KB=15728640 -v $RUNNER:/runner:ro -v $CORPUS:/rq2_queue:ro -v $OUT:/rq2_out --entrypoint /bin/bash $IMAGE /runner --binary $BINARY --checkpoints-ms 3600000 --seed-timeout 10 --out-prefix /rq2_out/r1_checkpoint60
EOF

set +e
docker run --rm --name sqlaser_sqlite_r1_cp60_smoke_${TS} \
  --cpuset-cpus "$CPU" --memory 4g \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --tmpfs /rq2_sqlite_tmp:rw,nosuid,nodev,size=256m \
  -e ROLLING_MERGE_INTERVAL=50 -e MIN_FREE_KB=15728640 \
  -v "$RUNNER":/runner:ro -v "$CORPUS":/rq2_queue:ro -v "$OUT":/rq2_out \
  --entrypoint /bin/bash "$IMAGE" /runner \
  --binary "$BINARY" --checkpoints-ms 3600000 --seed-timeout 10 \
  --out-prefix /rq2_out/r1_checkpoint60 \
  > "$OUT/container.stdout.log" 2> "$OUT/container.stderr.log"
rc=$?
set -e

end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
disk_after_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
coverage=${PREFIX}_t60.cov.json
profdata=${PREFIX}_t60.profdata
profile_meta=${PREFIX}_t60.meta.tsv
leak_summary=$PREFIX.db_leak_summary.json
coverage_size=0
[[ -f "$coverage" ]] && coverage_size=$(stat -c %s "$coverage")
host_leaks=$(find "$OUT" -maxdepth 2 -type f \( -name '*.db' -o -name '*.db-journal' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*-journal' -o -name '*-wal' -o -name '*-shm' \) | wc -l | tr -d ' ')

status=PASS
if [[ "$rc" -ne 0 || ! -s "$profdata" || ! -s "$coverage" || ! -s "$profile_meta" || ! -s "$leak_summary" || "$host_leaks" -ne 0 ]]; then
  status=FAIL
fi

cat > "$OUT/result.json" <<JSON
{
  "status": "$status",
  "docker_exit_code": $rc,
  "corpus": "$CORPUS",
  "seed_count": $seed_count,
  "profdata": "$profdata",
  "profile_meta": "$profile_meta",
  "coverage_json": "$coverage",
  "coverage_json_bytes": $coverage_size,
  "host_sqlite_temp_leaks": $host_leaks,
  "disk_free_before_kb": $disk_before_kb,
  "disk_free_after_kb": $disk_after_kb,
  "start_time": "$start_time",
  "end_time": "$end_time"
}
JSON

cat "$leak_summary" 2>/dev/null || true
cat "$OUT/result.json"
[[ "$status" = PASS ]]
