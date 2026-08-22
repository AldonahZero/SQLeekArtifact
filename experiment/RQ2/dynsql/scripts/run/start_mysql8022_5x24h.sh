#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/root/SQLeek/experiment/RQ2/dynsql
AFL=$ROOT/third_party/aflplusplus/afl-fuzz
HARNESS=$ROOT/scripts/run/mysql_afl_single_input.py
INSTALL=$ROOT/mysql/install-mysql-afl-min
SEEDS=$ROOT/seeds/mysql8022_initial
OUT_ROOT=$ROOT/output/mysql8022_5x24h
RUN_ROOT=$ROOT/runtime/mysql8022_5x24h
LOG_ROOT=$ROOT/logs/mysql8022_5x24h
DURATION=${MYSQL8022_DURATION:-86400}
TIMEOUT=${MYSQL8022_TIMEOUT:-120000}
MAX_STATEMENTS=${MYSQL8022_MAX_STATEMENTS:-20}
CPUS=(49 55 73 75 79)
RUNS=(r1 r2 r3 r4 r5)
PROTECTED_CPUS="6,10,17,20,23"

require_files() {
  test -x "$AFL"
  test -x "$HARNESS"
  test -x "$INSTALL/bin/mysqld"
  test -d "$SEEDS"
}

pg_guard() {
  local count
  count=$(tmux ls 2>/dev/null | grep -c 'dynsql_pg_5x24h_r' || true)
  if [[ "$count" -lt 5 ]]; then
    echo "PostgreSQL guard failed: expected 5 dynsql_pg_5x24h_r tmux sessions, found $count" >&2
    return 1
  fi
}

session_name() { echo "dynsql_mysql8022_5x24h_$1"; }

worker() {
  local run=$1 cpu=$2
  local out=$OUT_ROOT/$run
  local runtime=$RUN_ROOT/$run
  local logdir=$LOG_ROOT/$run
  mkdir -p "$runtime/tmp" "$logdir"
  chown mysql:mysql "$runtime" "$runtime/tmp" 2>/dev/null || true
  chmod 700 "$runtime/tmp" 2>/dev/null || true
  cd "$ROOT"
  echo "worker_start=$(date -Is)" | tee -a "$logdir/worker.log"
  echo "run=$run cpu=$cpu out=$out runtime=$runtime logdir=$logdir" | tee -a "$logdir/worker.log"
  exec timeout --kill-after=60s "$((DURATION + 120))" \
    taskset -c "$cpu" \
    env TMPDIR="$runtime/tmp" \
      AFL_IGNORE_PROBLEMS=1 \
      AFL_NO_FORKSRV=1 \
      AFL_SKIP_BIN_CHECK=1 \
      AFL_SKIP_CPUFREQ=1 \
      AFL_NO_UI=1 \
      "$AFL" -n -i "$SEEDS" -o "$out" -V "$DURATION" -t "$TIMEOUT" -m none -- \
        python3 "$HARNESS" \
          --input @@ \
          --install-dir "$INSTALL" \
          --runtime-root "$runtime" \
          --log-root "$logdir/mysql" \
          --max-statements "$MAX_STATEMENTS" \
          --timeout-seconds 20 \
          --quiet \
    >> "$logdir/afl-fuzz.log" 2>&1
}

start() {
  require_files
  pg_guard
  mkdir -p "$OUT_ROOT" "$RUN_ROOT" "$LOG_ROOT"
  for i in "${!RUNS[@]}"; do
    local run=${RUNS[$i]} cpu=${CPUS[$i]} sess
    sess=$(session_name "$run")
    if tmux has-session -t "$sess" 2>/dev/null; then
      echo "$sess already running"
      continue
    fi
    local out=$OUT_ROOT/$run
    if [[ -e "$out/default/fuzzer_stats" || -d "$out/default/queue" ]]; then
      local backup="$out.prev.$(date -u +%Y%m%d_%H%M%S)"
      mv "$out" "$backup"
      echo "preserved existing $out as $backup"
    fi
    mkdir -p "$RUN_ROOT/$run" "$LOG_ROOT/$run"
    tmux new-session -d -s "$sess" "bash '$0' __worker '$run' '$cpu'"
    echo "started $sess cpu=$cpu out=$out log=$LOG_ROOT/$run/afl-fuzz.log"
  done
}

status() {
  for i in "${!RUNS[@]}"; do
    local run=${RUNS[$i]} cpu=${CPUS[$i]} sess out stats execs eps paths crashes hangs pid
    sess=$(session_name "$run")
    out=$OUT_ROOT/$run
    stats=$out/default/fuzzer_stats
    if [[ ! -f "$stats" ]]; then stats=$out/fuzzer_stats; fi
    pid=$(tmux list-panes -t "$sess" -F '#{pane_pid}' 2>/dev/null || true)
    execs=NA; eps=NA; paths=NA; crashes=NA; hangs=NA
    if [[ -f "$stats" ]]; then
      execs=$(awk -F: '$1 ~ /^execs_done/ {gsub(/ /,"",$2); print $2}' "$stats" | tail -1)
      eps=$(awk -F: '$1 ~ /^execs_per_sec/ {gsub(/ /,"",$2); print $2}' "$stats" | tail -1)
      paths=$(awk -F: '$1 ~ /^(paths_total|corpus_count)/ {gsub(/ /,"",$2); print $2}' "$stats" | tail -1)
      crashes=$(awk -F: '$1 ~ /^unique_crashes/ {gsub(/ /,"",$2); print $2}' "$stats" | tail -1)
      hangs=$(awk -F: '$1 ~ /^unique_hangs/ {gsub(/ /,"",$2); print $2}' "$stats" | tail -1)
    elif [[ -f "$out/plot_data" ]]; then
      # AFL++ plot_data columns: unix_time, cycles_done, cur_path, paths_total, pending_total, pending_favs, map_size, unique_crashes, unique_hangs, max_depth, execs_per_sec, total_execs, ...
      last=$(tail -1 "$out/plot_data")
      paths=$(awk -F, '{gsub(/ /,"",$4); print $4}' <<<"$last")
      crashes=$(awk -F, '{gsub(/ /,"",$8); print $8}' <<<"$last")
      hangs=$(awk -F, '{gsub(/ /,"",$9); print $9}' <<<"$last")
      eps=$(awk -F, '{gsub(/ /,"",$11); print $11}' <<<"$last")
      execs=$(awk -F, '{gsub(/ /,"",$12); print $12}' <<<"$last")
    fi
    if tmux has-session -t "$sess" 2>/dev/null; then
      echo "$run LIVE session=$sess pane_pid=${pid:-NA} cpu=$cpu execs_done=$execs execs_per_sec=$eps paths=$paths crashes=$crashes hangs=$hangs out=$out log=$LOG_ROOT/$run/afl-fuzz.log"
    else
      echo "$run DOWN session=$sess cpu=$cpu execs_done=$execs execs_per_sec=$eps paths=$paths crashes=$crashes hangs=$hangs out=$out log=$LOG_ROOT/$run/afl-fuzz.log"
    fi
  done
}

stop() {
  for run in "${RUNS[@]}"; do
    local sess runtime
    sess=$(session_name "$run")
    runtime=$RUN_ROOT/$run
    if tmux has-session -t "$sess" 2>/dev/null; then
      tmux kill-session -t "$sess"
      echo "killed session $sess"
    fi
    # Only terminate MySQL/harness processes whose command line contains this run's independent runtime path.
    pgrep -af "$runtime|$LOG_ROOT/$run|mysql8022_5x24h/$run" | while read -r pid rest; do
      case "$rest" in
        *postgresql_5x24h*|*dynsql_pg_5x24h*) continue ;;
      esac
      kill "$pid" 2>/dev/null || true
    done
  done
}

case "${1:-}" in
  start) start ;;
  status) status ;;
  stop) stop ;;
  __worker) worker "$2" "$3" ;;
  *) echo "Usage: $0 {start|status|stop}" >&2; exit 2 ;;
esac
