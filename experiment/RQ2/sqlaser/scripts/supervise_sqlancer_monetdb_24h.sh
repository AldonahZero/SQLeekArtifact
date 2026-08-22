#!/usr/bin/env bash
set -e
set -u
set -o pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

ALL_MODE=0
INSTANCE=""
RUN_ROOT="/root/SQLeek/experiment/RQ2/live/monetdb_adapters/20260821_064823/sqlancer_monetdb_supervised"
AUTH_FILE="/root/SQLeek/experiment/RQ2/live/monetdb_adapters/20260821_064823/sqlancer_monetdb/monetdb_auth"
GEOM_IMAGE="rq2-monetdb-geom:20260821_1010"
SQLANCER_IMAGE="rq2-sqlancer-monetdb:20260821_071600"
DURATION_SECONDS=86400
POLL_SECONDS=2
RESTART_DELAY_SECONDS=5
READINESS_TIMEOUT_SECONDS=60
QUERY_TIMEOUT_SECONDS=60
NUM_TRIES=2147483647
MAX_GENERATED_DATABASES=-1
SEED_BASE=2026082100
MAX_EPOCHS=0
KEEP_SERVER_DB=0

SUPERVISOR_ROOT=""
SERVER_ROOT=""
SUPERVISOR_LOG=""
EVENTS_FILE=""
PID_FILE=""
LOCK_FILE=""
INSTANCE_ROOT=""
INSTANCE_SERVER_ROOT=""
active_server=""
active_client=""
current_epoch=""
current_epoch_dir=""
current_server_dir=""
current_epoch_start_ts=0
deadline_ts=0
instance_index=0
launched_pid=""

usage() {
    cat <<'USAGE'
Usage:
  supervise_sqlancer_monetdb_24h.sh --instance r1 [options]
  supervise_sqlancer_monetdb_24h.sh --all [options]

Each epoch has an independent MonetDB server and SQLancer client. When
either process exits, logs and exit codes are saved, the owned database is
removed by default, and a fresh epoch is started until the deadline.

Options:
  --instance r1|r2|r3|r4|r5
  --all
  --run-root PATH
  --auth-file PATH
  --geom-image IMAGE
  --sqlancer-image IMAGE
  --duration-seconds N
  --max-epochs N                 0 means unlimited
  --poll-seconds N
  --restart-delay-seconds N
  --readiness-timeout-seconds N
  --query-timeout-seconds N
  --num-tries N
  --max-generated-databases N    -1 means unlimited
  --seed-base N
  --keep-server-db
  -h|--help
USAGE
}

log() {
    local line
    line="[$(date '+%F %T%z')] $*"
    if [[ -n "$SUPERVISOR_LOG" ]]; then
        printf '%s\n' "$line" | tee -a "$SUPERVISOR_LOG"
    else
        printf '%s\n' "$line"
    fi
}

die() {
    log "ERROR: $*"
    exit 2
}

is_uint() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

is_int() {
    [[ "$1" =~ ^-?[0-9]+$ ]]
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --all)
                ALL_MODE=1
                shift
                ;;
            --instance)
                (( $# >= 2 )) || die "--instance needs a value"
                INSTANCE="$2"
                shift 2
                ;;
            --run-root)
                (( $# >= 2 )) || die "--run-root needs a value"
                RUN_ROOT="$2"
                shift 2
                ;;
            --auth-file)
                (( $# >= 2 )) || die "--auth-file needs a value"
                AUTH_FILE="$2"
                shift 2
                ;;
            --geom-image)
                (( $# >= 2 )) || die "--geom-image needs a value"
                GEOM_IMAGE="$2"
                shift 2
                ;;
            --sqlancer-image)
                (( $# >= 2 )) || die "--sqlancer-image needs a value"
                SQLANCER_IMAGE="$2"
                shift 2
                ;;
            --duration-seconds)
                (( $# >= 2 )) || die "--duration-seconds needs a value"
                DURATION_SECONDS="$2"
                shift 2
                ;;
            --max-epochs)
                (( $# >= 2 )) || die "--max-epochs needs a value"
                MAX_EPOCHS="$2"
                shift 2
                ;;
            --poll-seconds)
                (( $# >= 2 )) || die "--poll-seconds needs a value"
                POLL_SECONDS="$2"
                shift 2
                ;;
            --restart-delay-seconds)
                (( $# >= 2 )) || die "--restart-delay-seconds needs a value"
                RESTART_DELAY_SECONDS="$2"
                shift 2
                ;;
            --readiness-timeout-seconds)
                (( $# >= 2 )) || die "--readiness-timeout-seconds needs a value"
                READINESS_TIMEOUT_SECONDS="$2"
                shift 2
                ;;
            --query-timeout-seconds)
                (( $# >= 2 )) || die "--query-timeout-seconds needs a value"
                QUERY_TIMEOUT_SECONDS="$2"
                shift 2
                ;;
            --num-tries)
                (( $# >= 2 )) || die "--num-tries needs a value"
                NUM_TRIES="$2"
                shift 2
                ;;
            --max-generated-databases)
                (( $# >= 2 )) || die "--max-generated-databases needs a value"
                MAX_GENERATED_DATABASES="$2"
                shift 2
                ;;
            --seed-base)
                (( $# >= 2 )) || die "--seed-base needs a value"
                SEED_BASE="$2"
                shift 2
                ;;
            --keep-server-db)
                KEEP_SERVER_DB=1
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "unknown option: $1"
                ;;
        esac
    done
}

validate_common() {
    case "$RUN_ROOT" in
        /root/SQLeek/*) ;;
        *) die "--run-root must be below /root/SQLeek/: $RUN_ROOT" ;;
    esac
    case "$AUTH_FILE" in
        /root/SQLeek/*) ;;
        *) die "--auth-file must be below /root/SQLeek/: $AUTH_FILE" ;;
    esac
    command -v docker >/dev/null 2>&1 || die "docker is not available"
    command -v timeout >/dev/null 2>&1 || die "timeout is not available"
    command -v flock >/dev/null 2>&1 || die "flock is not available"
    command -v tar >/dev/null 2>&1 || die "tar is not available"
    [[ -f "$AUTH_FILE" ]] || die "MonetDB auth file does not exist: $AUTH_FILE"

    is_uint "$DURATION_SECONDS" && (( DURATION_SECONDS > 0 )) ||
        die "--duration-seconds must be a positive integer"
    is_uint "$POLL_SECONDS" && (( POLL_SECONDS > 0 )) ||
        die "--poll-seconds must be a positive integer"
    is_uint "$RESTART_DELAY_SECONDS" ||
        die "--restart-delay-seconds must be a non-negative integer"
    is_uint "$READINESS_TIMEOUT_SECONDS" && (( READINESS_TIMEOUT_SECONDS > 0 )) ||
        die "--readiness-timeout-seconds must be a positive integer"
    is_uint "$QUERY_TIMEOUT_SECONDS" && (( QUERY_TIMEOUT_SECONDS > 0 )) ||
        die "--query-timeout-seconds must be a positive integer"
    is_int "$NUM_TRIES" && (( NUM_TRIES > 0 )) ||
        die "--num-tries must be a positive integer"
    is_int "$MAX_GENERATED_DATABASES" ||
        die "--max-generated-databases must be an integer"
    is_uint "$SEED_BASE" ||
        die "--seed-base must be a non-negative integer"
    is_uint "$MAX_EPOCHS" ||
        die "--max-epochs must be a non-negative integer"

    docker image inspect "$GEOM_IMAGE" >/dev/null 2>&1 ||
        die "GEOM MonetDB image is unavailable: $GEOM_IMAGE"
    docker image inspect "$SQLANCER_IMAGE" >/dev/null 2>&1 ||
        die "SQLancer image is unavailable: $SQLANCER_IMAGE"

    SUPERVISOR_ROOT="$RUN_ROOT/supervisor"
    SERVER_ROOT="$RUN_ROOT/supervisor_servers"
    mkdir -p "$RUN_ROOT" "$SUPERVISOR_ROOT" "$SERVER_ROOT"
}

validate_instance() {
    [[ "$INSTANCE" =~ ^r[1-5]$ ]] ||
        die "--instance must be one of r1, r2, r3, r4, r5"
    case "$INSTANCE" in
        r1) instance_index=1 ;;
        r2) instance_index=2 ;;
        r3) instance_index=3 ;;
        r4) instance_index=4 ;;
        r5) instance_index=5 ;;
    esac
    INSTANCE_ROOT="$RUN_ROOT/$INSTANCE"
    INSTANCE_SERVER_ROOT="$SERVER_ROOT/$INSTANCE"
    mkdir -p "$INSTANCE_ROOT/supervisor_epochs" "$INSTANCE_ROOT/epoch_archives" "$INSTANCE_SERVER_ROOT"
}

container_state() {
    local name="$1"
    local result
    result="$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null)" ||
        result="missing"
    printf '%s\n' "$result"
}

container_exit_code() {
    local name="$1"
    local result
    result="$(docker inspect --format '{{.State.ExitCode}}' "$name" 2>/dev/null)" ||
        result="missing"
    printf '%s\n' "$result"
}

stop_owned_container() {
    local name="$1"
    local state
    [[ -n "$name" ]] || return 0
    state="$(container_state "$name")"
    case "$state" in
        running|created|paused|restarting)
            docker stop -t 10 "$name" >/dev/null 2>&1 ||
                docker kill "$name" >/dev/null 2>&1 ||
                true
            ;;
    esac
}

remove_owned_container() {
    local name="$1"
    [[ -n "$name" ]] || return 0
    docker rm -f "$name" >/dev/null 2>&1 || true
}

capture_container_artifacts() {
    local name="$1"
    local prefix="$2"
    [[ -n "$name" ]] || return 0
    docker logs "$name" > "$current_epoch_dir/$prefix.container.log" 2>&1 || true
    docker inspect "$name" > "$current_epoch_dir/$prefix.inspect.json" 2>&1 || true
}

archive_epoch_artifacts() {
    local epoch_label="$1"
    local archive_path="$INSTANCE_ROOT/epoch_archives/epoch-$epoch_label.tar.gz"
    if tar -czf "$archive_path" \
        -C "$INSTANCE_ROOT/supervisor_epochs" "epoch-$epoch_label" \
        2>> "$SUPERVISOR_LOG"; then
        rm -rf -- "$current_epoch_dir"
        log "archived epoch $epoch_label: $archive_path"
    else
        rm -f -- "$archive_path" || true
        log "WARNING: failed to archive epoch $epoch_label; retaining $current_epoch_dir"
    fi
}

wait_for_geom() {
    local limit
    limit=$(( $(date +%s) + READINESS_TIMEOUT_SECONDS ))
    while (( $(date +%s) < limit )); do
        [[ "$(container_state "$active_server")" == "running" ]] || return 1
        if timeout 5s docker exec "$active_server" \
            /opt/monetdb/bin/mclient -p 50101 \
            -s 'select count(*) from geometry_columns;' \
            > "$current_epoch_dir/geometry_check.out" 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

start_epoch() {
    local epoch="$1"
    local epoch_label
    printf -v epoch_label '%04d' "$epoch"
    current_epoch="$epoch"
    current_epoch_dir="$INSTANCE_ROOT/supervisor_epochs/epoch-$epoch_label"
    current_server_dir="$INSTANCE_SERVER_ROOT/epoch-$epoch_label/db_geom"
    mkdir -p "$current_epoch_dir" "$current_server_dir"

    printf -v active_server 'sqleek_sqlancer_monetdb_supervised_%s_server_epoch%s' "$INSTANCE" "$epoch_label"
    printf -v active_client 'sqleek_sqlancer_monetdb_supervised_%s_client_epoch%s' "$INSTANCE" "$epoch_label"
    remove_owned_container "$active_client"
    remove_owned_container "$active_server"

    log "starting epoch $epoch_label: server=$active_server"
    if ! docker run -d \
        --name "$active_server" \
        --label "sqleek.supervisor=sqlancer-monetdb" \
        --label "sqleek.run_root=$RUN_ROOT" \
        --label "sqleek.instance=$INSTANCE" \
        --label "sqleek.epoch=$epoch" \
        --restart=no \
        --entrypoint /opt/monetdb/bin/mserver5 \
        -e LD_LIBRARY_PATH=/opt/monetdb/lib:/opt/monetdb/lib/monetdb5 \
        -v "$current_server_dir:/home/monetdb/demo" \
        -v "$AUTH_FILE:/home/monetdb/.monetdb:ro" \
        "$GEOM_IMAGE" \
        --dbpath /home/monetdb/demo \
        --set mapi_port=50101 \
        --accept-the-risks-running-as-root \
        > "$current_epoch_dir/server.run.out" 2>&1; then
        log "epoch $epoch_label: docker run for server failed"
        return 1
    fi
    return 0
}

start_client() {
    local now
    local remaining
    local epoch_label
    local seed
    local database_prefix
    local client_command

    now="$(date +%s)"
    remaining=$(( deadline_ts - now ))
    (( remaining > 0 )) || return 1
    printf -v epoch_label '%04d' "$current_epoch"
    seed=$(( SEED_BASE + instance_index * 1000 + current_epoch ))
    printf -v database_prefix 'sqleek_%s_e%s_' "$INSTANCE" "$epoch_label"
    printf -v client_command 'exec timeout %ss java -jar /opt/sqlancer/sqlancer.jar --num-threads 1 --num-tries %s --max-generated-databases %s --timeout-seconds %s --host 127.0.0.1 --port 50101 --username monetdb --password monetdb --random-seed %s --database-prefix %s monetdb --oracle NOREC' \
        "$remaining" "$NUM_TRIES" "$MAX_GENERATED_DATABASES" "$QUERY_TIMEOUT_SECONDS" "$seed" "$database_prefix"

    log "starting epoch $epoch_label: client, remaining=$(printf '%ss' "$remaining"), seed=$seed"
    if ! docker run -d \
        --name "$active_client" \
        --label "sqleek.supervisor=sqlancer-monetdb" \
        --label "sqleek.run_root=$RUN_ROOT" \
        --label "sqleek.instance=$INSTANCE" \
        --label "sqleek.epoch=$current_epoch" \
        --restart=no \
        --network "container:$active_server" \
        -v "$current_epoch_dir:/workspace" \
        --entrypoint /bin/sh \
        "$SQLANCER_IMAGE" \
        -c "$client_command" \
        > "$current_epoch_dir/client.run.out" 2>&1; then
        log "epoch $epoch_label: docker run for SQLancer failed"
        return 1
    fi
    return 0
}

finish_epoch() {
    local reason="$1"
    local end_ts
    local server_before
    local client_before
    local server_after
    local client_after
    local server_exit
    local client_exit
    local elapsed
    local epoch_label

    [[ -n "$current_epoch" ]] || return 0
    end_ts="$(date +%s)"
    printf -v epoch_label '%04d' "$current_epoch"
    server_before="$(container_state "$active_server")"
    client_before="$(container_state "$active_client")"

    stop_owned_container "$active_client"
    stop_owned_container "$active_server"
    server_after="$(container_state "$active_server")"
    client_after="$(container_state "$active_client")"
    server_exit="$(container_exit_code "$active_server")"
    client_exit="$(container_exit_code "$active_client")"
    elapsed=$(( end_ts - current_epoch_start_ts ))

    capture_container_artifacts "$active_server" server
    capture_container_artifacts "$active_client" client
    {
        printf 'epoch=%s\n' "$current_epoch"
        printf 'instance=%s\n' "$INSTANCE"
        printf 'reason=%s\n' "$reason"
        printf 'start_epoch=%s\n' "$current_epoch_start_ts"
        printf 'end_epoch=%s\n' "$end_ts"
        printf 'elapsed_seconds=%s\n' "$elapsed"
        printf 'server_state_before_stop=%s\n' "$server_before"
        printf 'client_state_before_stop=%s\n' "$client_before"
        printf 'server_state_after_stop=%s\n' "$server_after"
        printf 'client_state_after_stop=%s\n' "$client_after"
        printf 'server_exit_code=%s\n' "$server_exit"
        printf 'client_exit_code=%s\n' "$client_exit"
        printf 'geom_image=%s\n' "$GEOM_IMAGE"
        printf 'sqlancer_image=%s\n' "$SQLANCER_IMAGE"
        printf 'num_tries=%s\n' "$NUM_TRIES"
        printf 'max_generated_databases=%s\n' "$MAX_GENERATED_DATABASES"
        printf 'server_db_dir=%s\n' "$current_server_dir"
    } > "$current_epoch_dir/summary.txt"

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date '+%F %T%z')" "$current_epoch" "$reason" \
        "$elapsed" "$server_after" "$server_exit" "$client_after" "$client_exit" \
        >> "$EVENTS_FILE"
    log "finished epoch $epoch_label: reason=$reason server=$server_after/$server_exit client=$client_after/$client_exit"

    remove_owned_container "$active_client"
    remove_owned_container "$active_server"
    if (( KEEP_SERVER_DB == 0 )); then
        if [[ "$current_server_dir" == "$INSTANCE_SERVER_ROOT"/* ]] &&
            [[ "$current_server_dir" != "$INSTANCE_SERVER_ROOT" ]] &&
            [[ "$current_server_dir" != "/" ]]; then
            rm -rf -- "$current_server_dir"
        else
            log "refusing to remove unexpected server db path: $current_server_dir"
        fi
    fi

    archive_epoch_artifacts "$epoch_label"

    active_client=""
    active_server=""
    current_epoch=""
    current_epoch_dir=""
    current_server_dir=""
}

cleanup_supervisor() {
    local rc="$?"
    set +e
    if [[ -n "$active_client" ]]; then
        stop_owned_container "$active_client"
        remove_owned_container "$active_client"
    fi
    if [[ -n "$active_server" ]]; then
        stop_owned_container "$active_server"
        remove_owned_container "$active_server"
    fi
    if [[ -n "$PID_FILE" ]]; then
        rm -f -- "$PID_FILE"
    fi
    exit "$rc"
}

init_instance() {
    SUPERVISOR_LOG="$SUPERVISOR_ROOT/$INSTANCE.supervisor.log"
    EVENTS_FILE="$SUPERVISOR_ROOT/$INSTANCE.events.tsv"
    PID_FILE="$SUPERVISOR_ROOT/$INSTANCE.pid"
    LOCK_FILE="$SUPERVISOR_ROOT/$INSTANCE.lock"
    if [[ -s "$PID_FILE" ]]; then
        local old_pid
        old_pid="$(< "$PID_FILE")"
        if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
            die "$INSTANCE already has a live supervisor (pid $old_pid)"
        fi
        rm -f -- "$PID_FILE"
    fi
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "$INSTANCE supervisor lock is held: $LOCK_FILE"
    printf '%s\n' "$$" > "$PID_FILE"
    if [[ ! -s "$EVENTS_FILE" ]]; then
        printf 'timestamp\tepoch\treason\telapsed_seconds\tserver_state\tserver_exit\tclient_state\tclient_exit\n' > "$EVENTS_FILE"
    fi
    trap cleanup_supervisor EXIT
    trap 'exit 143' INT TERM
}

run_instance() {
    local epoch=1
    local completed=0
    local reason
    local server_state
    local client_state
    local now

    deadline_ts=$(( $(date +%s) + DURATION_SECONDS ))
    log "supervisor started: instance=$INSTANCE duration=$(printf '%ss' "$DURATION_SECONDS") deadline=$deadline_ts"
    while (( $(date +%s) < deadline_ts )); do
        if (( MAX_EPOCHS > 0 && completed >= MAX_EPOCHS )); then
            break
        fi

        current_epoch_start_ts="$(date +%s)"
        if ! start_epoch "$epoch"; then
            finish_epoch "server_start_failed"
        elif ! wait_for_geom; then
            finish_epoch "geometry_not_ready"
        elif ! start_client; then
            finish_epoch "client_start_failed"
        else
            reason="deadline"
            while (( $(date +%s) < deadline_ts )); do
                server_state="$(container_state "$active_server")"
                client_state="$(container_state "$active_client")"
                if [[ "$server_state" != "running" ]]; then
                    reason="server_exited"
                    break
                fi
                if [[ "$client_state" != "running" ]]; then
                    reason="client_exited"
                    break
                fi
                sleep "$POLL_SECONDS"
            done
            finish_epoch "$reason"
        fi

        completed=$(( completed + 1 ))
        epoch=$(( epoch + 1 ))
        now="$(date +%s)"
        (( now < deadline_ts )) || break
        (( MAX_EPOCHS == 0 || completed < MAX_EPOCHS )) || break
        sleep "$RESTART_DELAY_SECONDS"
    done
    log "supervisor finished: instance=$INSTANCE epochs=$completed"
}

launch_one() {
    local instance="$1"
    local console_file="$2"
    if (( KEEP_SERVER_DB == 1 )); then
        nohup bash "$SCRIPT_PATH" --instance "$instance" \
            --run-root "$RUN_ROOT" --auth-file "$AUTH_FILE" \
            --geom-image "$GEOM_IMAGE" --sqlancer-image "$SQLANCER_IMAGE" \
            --duration-seconds "$DURATION_SECONDS" --max-epochs "$MAX_EPOCHS" \
            --poll-seconds "$POLL_SECONDS" --restart-delay-seconds "$RESTART_DELAY_SECONDS" \
            --readiness-timeout-seconds "$READINESS_TIMEOUT_SECONDS" \
            --query-timeout-seconds "$QUERY_TIMEOUT_SECONDS" \
            --num-tries "$NUM_TRIES" --max-generated-databases "$MAX_GENERATED_DATABASES" \
            --seed-base "$SEED_BASE" --keep-server-db \
            > "$console_file" 2>&1 < /dev/null &
    else
        nohup bash "$SCRIPT_PATH" --instance "$instance" \
            --run-root "$RUN_ROOT" --auth-file "$AUTH_FILE" \
            --geom-image "$GEOM_IMAGE" --sqlancer-image "$SQLANCER_IMAGE" \
            --duration-seconds "$DURATION_SECONDS" --max-epochs "$MAX_EPOCHS" \
            --poll-seconds "$POLL_SECONDS" --restart-delay-seconds "$RESTART_DELAY_SECONDS" \
            --readiness-timeout-seconds "$READINESS_TIMEOUT_SECONDS" \
            --query-timeout-seconds "$QUERY_TIMEOUT_SECONDS" \
            --num-tries "$NUM_TRIES" --max-generated-databases "$MAX_GENERATED_DATABASES" \
            --seed-base "$SEED_BASE" \
            > "$console_file" 2>&1 < /dev/null &
    fi
    launched_pid="$!"
}

launch_all() {
    local instance
    local pid_file
    local console_file
    local old_pid
    SUPERVISOR_LOG="$SUPERVISOR_ROOT/launcher.log"
    for instance in r1 r2 r3 r4 r5; do
        pid_file="$SUPERVISOR_ROOT/$instance.pid"
        if [[ -s "$pid_file" ]]; then
            old_pid="$(< "$pid_file")"
            if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
                log "skipping $instance: supervisor pid $old_pid is still live"
                continue
            fi
            rm -f -- "$pid_file"
        fi
        console_file="$SUPERVISOR_ROOT/$instance.console.log"
        launch_one "$instance" "$console_file"
        printf '%s\n' "$launched_pid" > "$SUPERVISOR_ROOT/$instance.launcher.pid"
        log "launched $instance supervisor pid=$launched_pid console=$console_file"
    done
}

parse_args "$@"
validate_common

if (( ALL_MODE == 1 )); then
    [[ -z "$INSTANCE" ]] || die "--all cannot be combined with --instance"
    launch_all
    exit 0
fi

validate_instance
init_instance
run_instance
