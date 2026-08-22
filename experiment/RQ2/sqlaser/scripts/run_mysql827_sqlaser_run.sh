#!/usr/bin/env bash
set -euo pipefail

image=${1:?image}
run_dir=${2:?run_dir}
mode=${3:?mode}
seconds_limit=${4:?seconds}
core=${5:?core}
oracle=${6:-NOREC}
target_path=${7:-/home/mysql/fuzzing/fuzz_root/sqlaser/mysql_target_chains.tsv}

mkdir -p "$run_dir/outputs" "$run_dir/bugs"
chmod 777 "$run_dir" "$run_dir/outputs" "$run_dir/bugs"

image_id=$(docker image inspect -f '{{.Id}}' "$image")
container="mysql827_sqlaser_$(basename "$run_dir")"
start_time=$(date -Is)

case "$mode" in
  sqlaser)
    env_args=(-e SQLASER_ENABLED=1 -e SQLASER_TARGETS="$target_path")
    ;;
  disabled)
    env_args=(-e SQLASER_ENABLED=0)
    ;;
  missing_target)
    env_args=(-e SQLASER_ENABLED=1 -e SQLASER_TARGETS=/home/mysql/fuzzing/fuzz_root/sqlaser/missing_target.tsv)
    ;;
  baseline)
    env_args=()
    ;;
  *)
    echo "unknown mode: $mode" >&2
    exit 2
    ;;
esac

cat > "$run_dir/run_manifest.json" <<JSON
{
  "image": "$image",
  "image_id": "$image_id",
  "mode": "$mode",
  "run_dir": "$run_dir",
  "cpu_core": $core,
  "oracle": "$oracle",
  "target_path": "$target_path",
  "mysql_version": "8.0.27",
  "mysql_commit": "3290a66c89eb1625a7058e0ef732432b6952b435",
  "sqlright_commit": "9457f0311b70562a3423ee86ac7e2ebdaaa6664b",
  "sqlaser_commit": "7b1decf9f1aac33e1eb55c8e5bc6cd683325db87",
  "sqlaser_patch": "/root/SQLeek/experiment/RQ2/sqlaser/patches/sqlaser_mysql827_distance_energy.patch",
  "distance_type": "sql_structure_proxy",
  "requested_runtime_seconds": $seconds_limit,
  "start_time": "$start_time",
  "cutoff_policy": "container wall time includes SQLRight helper startup"
}
JSON

printf '%s\n' "docker run -d --name $container ${env_args[*]} -v $run_dir/outputs:/home/mysql/fuzzing/fuzz_root/outputs -v $run_dir/bugs:/home/mysql/fuzzing/Bug_Analysis $image /bin/bash /home/mysql/scripts/run_sqlright_mysql_fuzzing_helper.sh --start-core $core --num-concurrent 1 -O $oracle" > "$run_dir/command.txt"

docker run -d --name "$container" "${env_args[@]}" \
  -v "$run_dir/outputs:/home/mysql/fuzzing/fuzz_root/outputs" \
  -v "$run_dir/bugs:/home/mysql/fuzzing/Bug_Analysis" \
  "$image" /bin/bash /home/mysql/scripts/run_sqlright_mysql_fuzzing_helper.sh \
  --start-core "$core" --num-concurrent 1 -O "$oracle" > "$run_dir/container_id.txt"

docker inspect "$container" > "$run_dir/container_inspect_start.json"
sleep "$seconds_limit"

set +e
docker stop -t 30 "$container" > "$run_dir/stop.log" 2>&1
stop_rc=$?
docker logs "$container" > "$run_dir/container.log" 2>&1
docker inspect "$container" > "$run_dir/container_inspect_end.json" 2>&1
docker rm "$container" > "$run_dir/remove.log" 2>&1
set -e

end_time=$(date -Is)
cat > "$run_dir/end_manifest.json" <<JSON
{
  "end_time": "$end_time",
  "docker_stop_rc": $stop_rc,
  "container_removed": true
}
JSON

exit 0
