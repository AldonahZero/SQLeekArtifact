#!/usr/bin/env bash
set -euo pipefail
run_dir=${1:?usage: run_checkpoint_replay.sh RUN_DIR}
replay_dir=${2:-$run_dir/checkpoint_replay/uniform_llvm_replay}
image=${SQLITE_REPLAY_IMAGE:-griffin_sqlite_llvmcov:latest}
binary=${SQLITE_REPLAY_BINARY:-/root/bin_aflpp/usr/local/bin/sqlite3}
seed_timeout=${SQLITE_REPLAY_SEED_TIMEOUT:-120}
checkpoints="60 180 300 480 600 720 900 1200 1440"
manifest="$run_dir/checkpoint_replay/checkpoint_manifest.json"
replay_script=/root/SQLeek/experiment/RQ2/replay/container_replay_llvm.sh
summary_script=/root/SQLeek/experiment/RQ2/sqlaser/scripts/summarize_sqlite_checkpoint_replay.py
[[ -s "$manifest" ]] || { echo "missing checkpoint manifest: $manifest" >&2; exit 1; }
docker image inspect "$image" >/dev/null
mkdir -p "$replay_dir"
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tversion\tstart_time\tend_time\n' > "$replay_dir/replay_index.tsv"
for cp in $checkpoints; do
  queue="$run_dir/checkpoint_replay/checkpoint_$(printf '%04d' "$cp")m/queue"
  out="$replay_dir/checkpoint_${cp}m"
  prefix="/rq2_out/sqlaser_sqlite_t${cp}"
  host_prefix="$out/sqlaser_sqlite_t${cp}"
  mkdir -p "$out"
  count=$(find "$queue" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
  start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set +e
  docker run --rm --privileged \
    -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
    -e LLVM_PROFDATA_BIN=llvm-profdata-12 -e LLVM_COV_BIN=llvm-cov-12 \
    -v "$queue:/rq2_queue:ro" -v "$out:/rq2_out" \
    -v "$replay_script:/rq2_replay.sh:ro" "$image" /bin/bash /rq2_replay.sh \
    --dbms sqlite --binary "$binary" --checkpoint-ms "$((cp * 60000))" \
    --seed-timeout "$seed_timeout" --out-prefix "$prefix" > "$out/docker.log" 2>&1
  rc=$?
  set -e
  end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  status=failed; message="docker_rc=$rc"; cov="$host_prefix.cov.json"; report="$host_prefix.report.txt"
  if [[ -s "$cov" ]]; then status=complete; message=; fi
  printf 'sqlaser_sqlite\tSQLaser\tsqlite\t1\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\tSQLite 3.54.0\t%s\t%s\n' \
    "$cp" "$cov" "$report" "$status" "$message" "$image" "$binary" "$count" "$queue" "$start" "$end" >> "$replay_dir/replay_index.tsv"
done
python3 "$summary_script" --replay-dir "$replay_dir" --run "$run_dir"
