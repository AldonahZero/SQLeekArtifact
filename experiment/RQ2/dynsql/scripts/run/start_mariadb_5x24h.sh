#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/root/SQLeek/experiment/RQ2/dynsql
AFL=$ROOT/third_party/aflplusplus/afl-fuzz
HARNESS=$ROOT/scripts/run/mariadb_afl_single_input.py
INSTALL=$ROOT/install/mariadb-smoke
SEEDS=$ROOT/seeds/mysql8022_initial
OUT_ROOT=$ROOT/output/mariadb_5x24h
RUN_ROOT=$ROOT/runtime/mariadb_5x24h
LOG_ROOT=$ROOT/logs/mariadb_5x24h
DURATION=${MARIADB_DURATION:-86400}
TIMEOUT=${MARIADB_TIMEOUT:-120000}
MAX_STATEMENTS=${MARIADB_MAX_STATEMENTS:-20}
RUNS=(r1 r2 r3 r4 r5)

require_files() {
  test -x "$AFL"
  test -x "$HARNESS"
  test -x "$INSTALL/bin/mariadbd"
  test -x "$INSTALL/bin/mariadb"
  test -x "$INSTALL/bin/mariadb-admin"
  test -x "$INSTALL/scripts/mariadb-install-db"
  test -d "$SEEDS"
  test "$(find "$SEEDS" -maxdepth 1 -type f | wc -l)" -gt 0
}

protected_guard() {
  local pg mysql
  pg=$(tmux ls 2>/dev/null | grep -c 'dynsql_pg_5x24h_r' || true)
  mysql=$(tmux ls 2>/dev/null | grep -c 'dynsql_mysql8022_5x24h_r' || true)
  if [[ "$pg" -lt 5 || "$mysql" -lt 5 ]]; then
    echo "Protection guard failed: PostgreSQL=$pg/5 MySQL=$mysql/5" >&2
    return 1
  fi
}

session_name() { echo "dynsql_mariadb_5x24h_$1"; }

worker() {
  local run=$1
  local out=$OUT_ROOT/$run
  local runtime=$RUN_ROOT/$run
  local logdir=$LOG_ROOT/$run
  mkdir -p "$runtime/tmp" "$logdir/mariadb"
  chown mysql:mysql "$runtime" "$runtime/tmp" "$logdir" "$logdir/mariadb" 2>/dev/null || true
  chmod 700 "$runtime/tmp" 2>/dev/null || true
  cd "$ROOT"
  {
    echo "worker_start=$(date -Is)"
    echo "run=$run out=$out runtime=$runtime logdir=$logdir"
    echo "cpu_affinity=disabled"
  } >> "$logdir/worker.log"
  exec timeout --kill-after=60s "$((DURATION + 120))" \
    env TMPDIR="$runtime/tmp" \
      AFL_IGNORE_PROBLEMS=1 \
      AFL_NO_AFFINITY=1 \
      AFL_NO_FORKSRV=1 \
      AFL_SKIP_BIN_CHECK=1 \
      AFL_SKIP_CPUFREQ=1 \
      AFL_NO_UI=1 \
      "$AFL" -n -i "$SEEDS" -o "$out" -V "$DURATION" -t "$TIMEOUT" -m none -- \
        python3 "$HARNESS" \
          --input @@ \
          --install-dir "$INSTALL" \
          --runtime-root "$runtime" \
          --log-root "$logdir/mariadb" \
          --max-statements "$MAX_STATEMENTS" \
          --timeout-seconds 20 \
          --quiet \
    >> "$logdir/afl-fuzz.log" 2>&1
}

start() {
  require_files
  protected_guard
  mkdir -p "$OUT_ROOT" "$RUN_ROOT" "$LOG_ROOT"
  for run in "${RUNS[@]}"; do
    local sess out
    sess=$(session_name "$run")
    out=$OUT_ROOT/$run
    if tmux has-session -t "$sess" 2>/dev/null; then
      echo "$sess already running"
      continue
    fi
    if [[ -f "$out/plot_data" || -d "$out/queue" ]]; then
      local backup="$out.prev.$(date -u +%Y%m%d_%H%M%S)"
      mv "$out" "$backup"
      echo "preserved existing $out as $backup"
    fi
    mkdir -p "$RUN_ROOT/$run" "$LOG_ROOT/$run"
    tmux new-session -d -s "$sess" "bash '$0' __worker '$run'"
    echo "started $sess out=$out runtime=$RUN_ROOT/$run log=$LOG_ROOT/$run/afl-fuzz.log"
  done
}

status() {
  for run in "${RUNS[@]}"; do
    local sess out plot pid last execs eps corpus crashes hangs
    sess=$(session_name "$run")
    out=$OUT_ROOT/$run
    plot=$out/plot_data
    pid=$(tmux list-panes -t "$sess" -F '#{pane_pid}' 2>/dev/null || true)
    execs=NA; eps=NA; corpus=NA; crashes=NA; hangs=NA
    if [[ -s "$plot" ]]; then
      last=$(grep -v '^#' "$plot" | tail -1 || true)
    fi
    if [[ -n "${last:-}" ]]; then
      corpus=$(awk -F, '{gsub(/ /,"",$4); print $4}' <<<"$last")
      crashes=$(awk -F, '{gsub(/ /,"",$8); print $8}' <<<"$last")
      hangs=$(awk -F, '{gsub(/ /,"",$9); print $9}' <<<"$last")
      eps=$(awk -F, '{gsub(/ /,"",$11); print $11}' <<<"$last")
      execs=$(awk -F, '{gsub(/ /,"",$12); print $12}' <<<"$last")
    fi
    if tmux has-session -t "$sess" 2>/dev/null; then
      echo "$run LIVE session=$sess pane_pid=${pid:-NA} execs_done=$execs execs_per_sec=$eps corpus=$corpus crashes=$crashes hangs=$hangs out=$out runtime=$RUN_ROOT/$run log=$LOG_ROOT/$run/afl-fuzz.log"
    else
      echo "$run DOWN session=$sess pane_pid=${pid:-NA} execs_done=$execs execs_per_sec=$eps corpus=$corpus crashes=$crashes hangs=$hangs out=$out runtime=$RUN_ROOT/$run log=$LOG_ROOT/$run/afl-fuzz.log"
    fi
  done
}

stop_run_processes() {
  local run=$1 runtime=$RUN_ROOT/$run pid args
  while read -r pid args; do
    [[ -n "${pid:-}" ]] || continue
    case "$args" in
      *"$runtime"*)
        case "$args" in
          *mariadbd*|*mariadb_afl_single_input.py*|*afl-fuzz*|*timeout*) kill -TERM "$pid" 2>/dev/null || true ;;
        esac
        ;;
    esac
  done < <(ps -eo pid=,args=)
}

stop() {
  for run in "${RUNS[@]}"; do
    local sess
    sess=$(session_name "$run")
    if tmux has-session -t "$sess" 2>/dev/null; then
      tmux kill-session -t "$sess"
      echo "killed session $sess"
    fi
    stop_run_processes "$run"
  done
}

case "${1:-}" in
  start) start ;;
  status) status ;;
  stop) stop ;;
  __worker) worker "$2" ;;
  *) echo "Usage: $0 {start|status|stop}" >&2; exit 2 ;;
esac
