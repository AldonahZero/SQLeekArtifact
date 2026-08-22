#!/usr/bin/env bash
set -euo pipefail

# Host-side launcher for clean replay of the stage-3 MariaDB bug samples.
# The same file is mounted into each isolated replay container and invoked
# with --container.  Runs are intentionally scheduled in pairs.

usage() {
  cat >&2 <<'EOF'
Usage:
  replay_mariadb_bug_samples.sh --campaign DIR --run RUN_DIR [--run RUN_DIR ...]

Environment:
  REPLAY_IMAGE   MariaDB stage-3 image (default: sqleek-mariadb:mariadb-stage3-20260716-staged)
  REPLAY_MEMORY  Per-replay-container Docker memory limit (default: 8g)
  REPLAY_TIMEOUT Per-candidate SQL timeout in seconds (default: 120)
EOF
}

container_replay() {
  local input_dir="${REPLAY_INPUT_DIR:-/workspace/replay_inputs}"
  local output_dir="${REPLAY_OUTPUT_DIR:-/workspace/replay_out}"
  local replay_timeout="${REPLAY_TIMEOUT:-120}"
  local mysqld=/opt/dbms/bin/mysqld
  local mysql=/opt/dbms/bin/mysql
  local mysqladmin=/opt/dbms/bin/mysqladmin
  local datadir=/tmp/sqleek_replay_datadir
  local socket=/tmp/sqleek_replay.sock
  local pid_file=/tmp/sqleek_replay.pid
  local server_pid=""
  local sample_index=0

  mkdir -p "$output_dir/server_logs" "$output_dir/sql_stdout" "$output_dir/sql_stderr"
  printf 'sample_index\tsample_sha256\tsource_file\tstatus\tclient_rc\n' \
    > "$output_dir/results.tsv"

  stop_server() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" >/dev/null 2>&1; then
      kill -TERM "$server_pid" >/dev/null 2>&1 || true
      for _ in $(seq 1 20); do
        kill -0 "$server_pid" >/dev/null 2>&1 || break
        sleep 0.5
      done
      kill -KILL "$server_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "$server_pid" ]]; then
      wait "$server_pid" >/dev/null 2>&1 || true
    fi
    server_pid=""
    rm -f "$socket" "$pid_file"
  }

  trap stop_server EXIT

  start_server() {
    stop_server
    rm -rf "$datadir"
    mkdir -p "$datadir"
    cp -a /opt/dbms/data_all/ori_data/. "$datadir/"
    chown -R mysql:mysql "$datadir"

    "$mysqld" \
      --basedir=/opt/dbms \
      --datadir="$datadir" \
      --port=9000 \
      --socket="$socket" \
      --pid-file="$pid_file" \
      --performance_schema=OFF \
      --skip-networking=0 \
      --user=mysql \
      --log-error="$current_server_log" \
      >/dev/null 2>&1 &
    server_pid=$!

    for _ in $(seq 1 60); do
      if "$mysqladmin" --protocol=socket --socket="$socket" -uroot ping \
        >/dev/null 2>&1; then
        "$mysql" --protocol=socket --socket="$socket" -uroot \
          -e 'CREATE DATABASE IF NOT EXISTS test_init; CREATE DATABASE IF NOT EXISTS test_sqlright1; FLUSH PRIVILEGES;' \
          >/dev/null 2>&1 || true
        return 0
      fi
      sleep 0.5
    done
    return 1
  }

  server_alive() {
    [[ -n "$server_pid" ]] || return 1
    kill -0 "$server_pid" >/dev/null 2>&1 || return 1
    "$mysqladmin" --protocol=socket --socket="$socket" -uroot ping \
      >/dev/null 2>&1
  }

  while IFS= read -r -d '' sample; do
    sample_index=$((sample_index + 1))
    sample_name="${sample##*/}"
    sample_sha256="$(sha256sum "$sample" | cut -d ' ' -f 1)"
    sample_id="$(printf '%04d_%s' "$sample_index" "${sample_sha256:0:16}")"
    sql_file="/tmp/${sample_id}.sql"
    current_server_log="$output_dir/server_logs/${sample_id}.log"
    stdout_file="$output_dir/sql_stdout/${sample_id}.txt"
    stderr_file="$output_dir/sql_stderr/${sample_id}.txt"

    awk '
      NR == 1 && $0 ~ /^Query:[[:space:]]*$/ { next }
      $0 ~ /^Result NUM:[[:space:]]*/ { exit }
      { print }
    ' "$sample" > "$sql_file"

    status="START_FAILED"
    client_rc=125
    if [[ -s "$sql_file" ]] && start_server; then
      set +e
      timeout --signal=TERM --kill-after=10 "$replay_timeout" \
        "$mysql" --binary-mode=1 --force --protocol=socket \
        --socket="$socket" -uroot test_sqlright1 \
        < "$sql_file" > "$stdout_file" 2> "$stderr_file"
      client_rc=$?
      set -e

      if ! server_alive; then
        status="CRASH"
      elif [[ "$client_rc" -eq 124 || "$client_rc" -eq 137 ]]; then
        status="TIMEOUT"
      elif [[ "$client_rc" -eq 0 ]]; then
        status="OK"
      else
        status="SQL_ERROR"
      fi
    elif [[ ! -s "$sql_file" ]]; then
      status="EMPTY_SQL"
    fi

    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$sample_index" "$sample_sha256" "$sample_name" "$status" "$client_rc" \
      >> "$output_dir/results.tsv"
    stop_server
    rm -f "$sql_file"
  done < <(find "$input_dir" -maxdepth 1 -type f -size +0c -print0 | sort -z)

  {
    printf 'completed_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'sample_count\t%s\n' "$sample_index"
    for status in CRASH TIMEOUT START_FAILED SQL_ERROR OK EMPTY_SQL; do
      count="$(awk -F '\t' -v wanted="$status" 'NR > 1 && $4 == wanted {n++} END {print n + 0}' "$output_dir/results.tsv")"
      printf '%s\t%s\n' "$status" "$count"
    done
  } > "$output_dir/summary.tsv"
  touch "$output_dir/COMPLETE"
  # The EXIT trap closes over function locals.  Disable it after normal
  # completion so bash -u does not re-enter it after container_replay returns.
  trap - EXIT
}

host_launcher() {
  local campaign=""
  local image="${REPLAY_IMAGE:-sqleek-mariadb:mariadb-stage3-20260716-staged}"
  local memory="${REPLAY_MEMORY:-8g}"
  local script_path
  local -a run_dirs=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --campaign)
        campaign="$2"
        shift 2
        ;;
      --run)
        run_dirs+=("$2")
        shift 2
        ;;
      --image)
        image="$2"
        shift 2
        ;;
      --memory)
        memory="$2"
        shift 2
        ;;
      --help|-h)
        usage
        return 0
        ;;
      *)
        echo "unknown argument: $1" >&2
        usage
        return 2
        ;;
    esac
  done

  [[ -n "$campaign" ]] || { usage; return 2; }
  [[ ${#run_dirs[@]} -gt 0 ]] || { usage; return 2; }
  script_path="$(readlink -f "$0")"
  mkdir -p "$campaign/logs"
  docker image inspect "$image" >/dev/null

  printf 'run_id\tinput_dir\toutput_dir\tcontainer\tpair\n' \
    > "$campaign/replay_manifest.tsv"

  local pair_index=0
  local pair_count=0
  local -a pair_runs=()
  local -a pair_containers=()

  wait_pair() {
    local i container run_id wait_output wait_rc
    for i in "${!pair_containers[@]}"; do
      container="${pair_containers[$i]}"
      run_id="${pair_runs[$i]}"
      wait_output=""
      wait_rc=0
      wait_output="$(docker wait "$container" 2>&1)" || wait_rc=$?
      printf '%s\t%s\t%s\t%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$container" "$wait_output" \
        >> "$campaign/replay_launcher.tsv"
      docker logs "$container" > "$campaign/logs/${run_id}.container.log" 2>&1 || true
      if [[ "$wait_rc" -ne 0 ]]; then
        echo "replay container failed to wait: $container rc=$wait_rc" >&2
      fi
    done
    pair_runs=()
    pair_containers=()
  }

  printf 'timestamp_utc\trun_id\tcontainer\texit_output\n' \
    > "$campaign/replay_launcher.tsv"

  for run_dir in "${run_dirs[@]}"; do
    [[ -d "$run_dir/runtime/Bug_Analysis/bug_samples" ]] || {
      echo "missing bug_samples: $run_dir" >&2
      return 3
    }
    run_id="$(basename "$run_dir")"
    output_dir="$campaign/$run_id"
    container="sqleek_replay_mariadb_${run_id}"
    mkdir -p "$output_dir"
    if docker ps -a --format '{{.Names}}' | grep -Fxq "$container"; then
      echo "replay container already exists: $container" >&2
      return 3
    fi

    pair_count=$((pair_count + 1))
    pair_id=$(( (pair_count + 1) / 2 ))
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$run_id" "$run_dir/runtime/Bug_Analysis/bug_samples" "$output_dir" "$container" "$pair_id" \
      >> "$campaign/replay_manifest.tsv"
    docker run -d \
      --name "$container" \
      --label org.sqleek.experiment=rq4_wo_m1_replay \
      --label org.sqleek.dbms=mariadb \
      --label org.sqleek.run_id="$run_id" \
      --memory "$memory" \
      -e REPLAY_INPUT_DIR=/workspace/replay_inputs \
      -e REPLAY_OUTPUT_DIR=/workspace/replay_out \
      -e REPLAY_TIMEOUT="${REPLAY_TIMEOUT:-120}" \
      -v "$run_dir/runtime/Bug_Analysis/bug_samples:/workspace/replay_inputs:ro" \
      -v "$output_dir:/workspace/replay_out" \
      -v "$script_path:/workspace/replay_mariadb_bug_samples.sh:ro" \
      --entrypoint /bin/bash \
      "$image" -lc '/workspace/replay_mariadb_bug_samples.sh --container' \
      >> "$campaign/logs/${run_id}.docker-run.log"

    pair_runs+=("$run_id")
    pair_containers+=("$container")
    if [[ ${#pair_containers[@]} -eq 2 ]]; then
      pair_index=$((pair_index + 1))
      printf '%s\tPAIR_START\t%s\t%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$pair_index" "${pair_runs[*]}" \
        >> "$campaign/replay_launcher.tsv"
      wait_pair
      printf '%s\tPAIR_DONE\t%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$pair_index" \
        >> "$campaign/replay_launcher.tsv"
    fi
  done

  if [[ ${#pair_containers[@]} -gt 0 ]]; then
    pair_index=$((pair_index + 1))
    printf '%s\tPAIR_START\t%s\t%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$pair_index" "${pair_runs[*]}" \
      >> "$campaign/replay_launcher.tsv"
    wait_pair
    printf '%s\tPAIR_DONE\t%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$pair_index" \
      >> "$campaign/replay_launcher.tsv"
  fi
  touch "$campaign/COMPLETE"
}

if [[ "${1:-}" == "--container" ]]; then
  shift
  container_replay "$@"
else
  host_launcher "$@"
fi
