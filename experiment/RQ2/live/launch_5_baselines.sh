#!/usr/bin/env bash
set -u
BASE=/root/SQLeek/experiment/RQ2/live
LOG=$BASE/logs
SQL=$BASE/sqlancer
mkdir -p "$LOG" "$SQL" "$BASE/notes"
TS=$(date -u +%Y%m%d_%H%M%S)
MANIFEST=$BASE/notes/launch_${TS}.tsv
printf "kind\tname\tdbms\tpid_or_container\tlog\tstarted_at\n" > "$MANIFEST"

start_sqlancer_sqlite() {
  local r=$1
  local name="rq2_sqlancer_sqlite_r${r}_${TS}"
  local dir="$SQL/$name"
  mkdir -p "$dir"
  (
    cd "$dir"
    nohup timeout 24h java -jar /root/sy/sqlancer/target/sqlancer-2.0.0.jar \
      --num-threads 1 \
      --random-seed "$((2026061500 + r))" \
      --timeout-seconds 86400 \
      --database-prefix "$name" \
      sqlite3 > "$LOG/${name}.log" 2>&1 &
    echo $! > "$BASE/${name}.pid"
    printf "sqlancer\t%s\tsqlite\t%s\t%s\t%s\n" "$name" "$(cat "$BASE/${name}.pid")" "$LOG/${name}.log" "$(date -Is)" >> "$MANIFEST"
  )
}

start_squirrel_container() {
  local dbms=$1
  local r=$2
  local seed_dir=$3
  local mutator=$4
  local image="griffin_${dbms}"
  local name="rq2_squirrel_${dbms}_r${r}_${TS}"
  local log_file="$LOG/${name}.log"
  {
    echo "[$(date -Is)] docker run $name ($image)"
    docker run --privileged -itd -m 70G --cpus=10 --shm-size=5G \
      -e GRIFFIN_CONTAINER=1 \
      -e AFL_CUSTOM_MUTATOR_LIBRARY="$mutator" \
      -e SQUIRREL_DISABLE_MERGE=1 \
      --name "$name" "$image"
    echo "[$(date -Is)] copy seeds from $seed_dir"
    docker exec "$name" rm -rf /workspace/seeds
    docker exec "$name" mkdir -p /workspace/seeds
    docker cp "$seed_dir"/. "$name":/workspace/seeds/
    echo "[$(date -Is)] start_all"
    docker exec "$name" /workspace/scripts/start_all.sh
    echo "[$(date -Is)] started"
  } > "$log_file" 2>&1
  printf "squirrel\t%s\t%s\t%s\t%s\t%s\n" "$name" "$dbms" "$name" "$log_file" "$(date -Is)" >> "$MANIFEST"
}

# Three lightweight SQLancer runs.
for r in 1 2 3; do
  start_sqlancer_sqlite "$r"
done

# Two SQUIRREL/Griffin runs. Keep this small to avoid overloading the server.
DF=/root/dfuzz-griffin/docker/metadata_collector/input-set/input-set_for_squirrel
start_squirrel_container postgres 1 "$DF/sqlite_default" \
  /workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_sqlite.so
start_squirrel_container mysql 1 "$DF/postgres_default" \
  /workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_postgres.so

echo "$MANIFEST"
