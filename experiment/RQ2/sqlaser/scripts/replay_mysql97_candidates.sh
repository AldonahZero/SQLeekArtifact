#!/usr/bin/env bash
set -euo pipefail

IMAGE="mysql:9.7.0"
MANIFEST="/root/SQLeek/experiment/RQ2/sqlaser/results/mysql827/sqlaser_prototype/formal_24h/triage/dedup_20260713_125005/candidate_manifest.tsv"
KIND="afl_crash"
CORPUS_DIR=""
OUT_DIR=""
LIMIT="0"
SEED_TIMEOUT="20s"
STARTUP_TIMEOUT="120"
MIN_FREE_GB="15"
DB_NAME="sqlaser_replay"
INPUT_FORMAT="raw"
KEEP_CONTAINER_ON_EXIT="0"

usage() {
  cat <<'USAGE'
Usage:
  replay_mysql97_candidates.sh [options]

Options:
  --image IMAGE              Docker image, default mysql:9.7.0
  --manifest FILE            candidate_manifest.tsv source
  --kind KIND                manifest kind, default afl_crash
  --corpus-dir DIR           use files from DIR instead of manifest
  --out-dir DIR              output directory
  --limit N                  replay only first N seeds, 0 means all
  --seed-timeout DURATION    timeout per seed, default 20s
  --startup-timeout SECONDS  mysqld startup wait, default 120
  --min-free-gb N            stop before replay when free space below N GB
  --db-name NAME             replay database name, default sqlaser_replay
  --input-format raw|sqlright-bug-sample
  --keep-container           leave final replay container running
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --kind) KIND="$2"; shift 2 ;;
    --corpus-dir) CORPUS_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    --startup-timeout) STARTUP_TIMEOUT="$2"; shift 2 ;;
    --min-free-gb) MIN_FREE_GB="$2"; shift 2 ;;
    --db-name) DB_NAME="$2"; shift 2 ;;
    --input-format) INPUT_FORMAT="$2"; shift 2 ;;
    --keep-container) KEEP_CONTAINER_ON_EXIT="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$OUT_DIR" ]]; then
  ts="$(date -u +%Y%m%d_%H%M%S)"
  OUT_DIR="/root/SQLeek/experiment/RQ2/sqlaser/results/mysql827/sqlaser_prototype/formal_24h/triage/mysql97_replay_${ts}"
fi

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "unsafe db name: $DB_NAME" >&2
  exit 2
fi

if [[ "$INPUT_FORMAT" != "raw" && "$INPUT_FORMAT" != "sqlright-bug-sample" ]]; then
  echo "unsupported input format: $INPUT_FORMAT" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"/{logs,seed_stdout,seed_stderr,server_crash_candidates,prepared_sql,tmp}
SEED_LIST="$OUT_DIR/seed_list.txt"
SUMMARY="$OUT_DIR/replay_summary.tsv"
MANIFEST_OUT="$OUT_DIR/run_manifest.json"
EVENT_LOG="$OUT_DIR/events.log"

log_event() {
  printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$EVENT_LOG" >&2
}

free_gb() {
  df -BG /root/SQLeek | awk 'NR==2 {gsub(/G/, "", $4); print $4}'
}

check_free_space() {
  local free
  free="$(free_gb)"
  if [[ "$free" -lt "$MIN_FREE_GB" ]]; then
    log_event "STOP low_disk_space free_gb=$free min_free_gb=$MIN_FREE_GB"
    exit 75
  fi
}

if [[ -n "$CORPUS_DIR" ]]; then
  find "$CORPUS_DIR" -type f -print | sort > "$SEED_LIST"
else
  awk -F'\t' -v kind="$KIND" 'NR > 1 && $1 == kind {print $3}' "$MANIFEST" > "$SEED_LIST"
fi

if [[ "$LIMIT" != "0" ]]; then
  head -n "$LIMIT" "$SEED_LIST" > "$SEED_LIST.limit"
  mv "$SEED_LIST.limit" "$SEED_LIST"
fi

SEED_COUNT="$(wc -l < "$SEED_LIST" | tr -d ' ')"
if [[ "$SEED_COUNT" -eq 0 ]]; then
  echo "no seeds selected" >&2
  exit 2
fi

IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
IMAGE_CREATED="$(docker image inspect "$IMAGE" --format '{{.Created}}')"
MYSQL_VERSION="$(docker run --rm "$IMAGE" mysqld --version 2>&1 | tr '\n' ' ')"
HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
START_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$MANIFEST_OUT" <<EOF
{
  "start_time": "$START_TIME",
  "host": "$HOSTNAME_FQDN",
  "image": "$IMAGE",
  "image_id": "$IMAGE_ID",
  "image_created": "$IMAGE_CREATED",
  "mysql_version": "$MYSQL_VERSION",
  "manifest": "$MANIFEST",
  "kind": "$KIND",
  "corpus_dir": "$CORPUS_DIR",
  "input_format": "$INPUT_FORMAT",
  "seed_count": $SEED_COUNT,
  "seed_timeout": "$SEED_TIMEOUT",
  "db_name": "$DB_NAME",
  "min_free_gb": $MIN_FREE_GB
}
EOF

printf 'idx\tseed_path\tsha256\tbytes\tstatus\trc\telapsed_sec\tpre_ping\tpre_query\tpost_ping\tpost_query\treset_after\tstdout\tstderr\n' > "$SUMMARY"

CONTAINER=""

container_running() {
  [[ -n "$CONTAINER" ]] && docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx 'true'
}

mysql_ping() {
  container_running && docker exec "$CONTAINER" mysqladmin --protocol=socket -uroot ping --silent >/dev/null 2>&1
}

mysql_query_alive() {
  container_running && docker exec "$CONTAINER" mysql --protocol=socket -uroot -N -B -e 'SELECT 1' >/dev/null 2>&1
}

save_container_state() {
  local prefix="$1"
  if [[ -z "$CONTAINER" ]]; then
    return 0
  fi
  docker logs "$CONTAINER" > "$OUT_DIR/logs/${prefix}.docker.log" 2>&1 || true
  docker inspect "$CONTAINER" > "$OUT_DIR/logs/${prefix}.inspect.json" 2>/dev/null || true
}

stop_container() {
  if [[ -n "$CONTAINER" ]] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
    save_container_state "stop_${CONTAINER}"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  CONTAINER=""
}

cleanup() {
  if [[ "$KEEP_CONTAINER_ON_EXIT" == "1" ]]; then
    log_event "KEEP_CONTAINER container=$CONTAINER"
  else
    stop_container
  fi
}
trap cleanup EXIT

start_container() {
  stop_container
  local suffix
  suffix="$(date -u +%Y%m%d%H%M%S)_$$"
  CONTAINER="sqlaser_mysql97_replay_${suffix}"
  log_event "START_CONTAINER name=$CONTAINER image=$IMAGE"
  docker run -d \
    --name "$CONTAINER" \
    --network none \
    --tmpfs /var/lib/mysql:rw,size=6g \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    -e MYSQL_ALLOW_EMPTY_PASSWORD=1 \
    -e MYSQL_DATABASE="$DB_NAME" \
    "$IMAGE" \
    --skip-networking=ON \
    --local-infile=0 \
    --secure-file-priv=/tmp >/dev/null

  local deadline now
  deadline=$(( $(date +%s) + STARTUP_TIMEOUT ))
  while true; do
    if mysql_ping && mysql_query_alive; then
      log_event "CONTAINER_READY name=$CONTAINER"
      reset_database >/dev/null 2>&1 || true
      return 0
    fi
    now="$(date +%s)"
    if [[ "$now" -ge "$deadline" ]]; then
      save_container_state "startup_failed_${CONTAINER}"
      echo "mysqld startup timeout for $CONTAINER" >&2
      exit 70
    fi
    sleep 2
  done
}

reset_database() {
  docker exec "$CONTAINER" mysql --protocol=socket -uroot -e "DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\`;" >/dev/null 2>&1
}

prepare_sql() {
  local seed="$1"
  local prepared="$2"
  if [[ "$INPUT_FORMAT" == "raw" ]]; then
    printf '%s\n' "$seed"
    return 0
  fi
  awk '
    /^Query:[[:space:]]*$/ { in_query=1; next }
    /^Query:/ { in_query=1; sub(/^Query:[[:space:]]*/, ""); print; next }
    /^Result string:/ { exit }
    in_query { print }
  ' "$seed" > "$prepared"
  printf '%s\n' "$prepared"
}

start_container

idx=0
ok_count=0
client_error_count=0
timeout_count=0
server_crash_count=0
reset_error_count=0

while IFS= read -r seed; do
  idx=$((idx + 1))
  check_free_space
  if [[ ! -f "$seed" ]]; then
    log_event "MISSING_SEED idx=$idx path=$seed"
    continue
  fi

  sha="$(sha256sum "$seed" | awk '{print $1}')"
  bytes="$(stat -c '%s' "$seed")"
  stdout="$OUT_DIR/seed_stdout/${idx}_${sha}.out"
  stderr="$OUT_DIR/seed_stderr/${idx}_${sha}.err"
  prepared="$OUT_DIR/prepared_sql/${idx}_${sha}.sql"
  sql_path="$(prepare_sql "$seed" "$prepared")"

  pre_ping=0
  pre_query=0
  post_ping=0
  post_query=0
  reset_after=0
  status="unknown"

  mysql_ping && pre_ping=1 || pre_ping=0
  mysql_query_alive && pre_query=1 || pre_query=0
  if [[ "$pre_ping" != "1" || "$pre_query" != "1" ]]; then
    save_container_state "pre_dead_idx_${idx}_${sha}"
    start_container
    mysql_ping && pre_ping=1 || pre_ping=0
    mysql_query_alive && pre_query=1 || pre_query=0
  fi

  reset_database || {
    save_container_state "reset_before_failed_idx_${idx}_${sha}"
    start_container
    reset_database
  }

  start_sec="$(date +%s)"
  set +e
  timeout --kill-after=5s "$SEED_TIMEOUT" docker exec -i "$CONTAINER" \
    mysql --protocol=socket -uroot --binary-mode=1 --force "$DB_NAME" \
    < "$sql_path" > "$stdout" 2> "$stderr"
  rc=$?
  set -e
  end_sec="$(date +%s)"
  elapsed=$((end_sec - start_sec))

  mysql_ping && post_ping=1 || post_ping=0
  mysql_query_alive && post_query=1 || post_query=0

  if [[ "$post_ping" != "1" || "$post_query" != "1" ]]; then
    status="server_crash_candidate"
    server_crash_count=$((server_crash_count + 1))
    cp -a "$seed" "$OUT_DIR/server_crash_candidates/${idx}_${sha}.seed"
    [[ "$sql_path" != "$seed" ]] && cp -a "$sql_path" "$OUT_DIR/server_crash_candidates/${idx}_${sha}.sql"
    save_container_state "server_crash_idx_${idx}_${sha}"
    start_container
    reset_after=1
  elif [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    status="timeout"
    timeout_count=$((timeout_count + 1))
    reset_database && reset_after=1 || {
      reset_error_count=$((reset_error_count + 1))
      save_container_state "reset_after_timeout_failed_idx_${idx}_${sha}"
      start_container
      reset_after=1
    }
  elif [[ "$rc" -ne 0 ]]; then
    status="client_error"
    client_error_count=$((client_error_count + 1))
    reset_database && reset_after=1 || {
      reset_error_count=$((reset_error_count + 1))
      save_container_state "reset_after_client_error_failed_idx_${idx}_${sha}"
      start_container
      reset_after=1
    }
  else
    status="ok"
    ok_count=$((ok_count + 1))
    reset_database && reset_after=1 || {
      reset_error_count=$((reset_error_count + 1))
      save_container_state "reset_after_ok_failed_idx_${idx}_${sha}"
      start_container
      reset_after=1
    }
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$idx" "$seed" "$sha" "$bytes" "$status" "$rc" "$elapsed" \
    "$pre_ping" "$pre_query" "$post_ping" "$post_query" "$reset_after" \
    "$stdout" "$stderr" >> "$SUMMARY"
  log_event "SEED idx=$idx status=$status rc=$rc elapsed=$elapsed sha=$sha"
done < "$SEED_LIST"

END_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$OUT_DIR/final_summary.txt" <<EOF
end_time=$END_TIME
seeds_total=$SEED_COUNT
seeds_processed=$idx
ok=$ok_count
client_error=$client_error_count
timeout=$timeout_count
server_crash_candidate=$server_crash_count
reset_error=$reset_error_count
summary_tsv=$SUMMARY
EOF

log_event "DONE processed=$idx ok=$ok_count client_error=$client_error_count timeout=$timeout_count server_crash_candidate=$server_crash_count reset_error=$reset_error_count"
