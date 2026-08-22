#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/SQLeek/experiment/RQ2/dynsql
SCRIPT="$ROOT/scripts/run/start_postgresql_5x24h.sh"
AFL="$ROOT/third_party/aflplusplus/afl-fuzz"
TARGET="$ROOT/scripts/run/postgresql_afl_single_input.py"
SEEDS="$ROOT/seeds/initial"
OUT_BASE="$ROOT/output/postgresql_5x24h"
RUN_BASE="$ROOT/runtime/postgresql_5x24h"
LOG_BASE="$ROOT/logs/postgresql_5x24h"
STATE_DIR="$LOG_BASE/state"
DURATION=86400
RUNS=(r1 r2 r3 r4 r5)
CPUS=(6 10 17 20 23)

session_name() {
  printf 'dynsql_pg_5x24h_%s' "$1"
}

stats_path() {
  local run=$1
  local out="$OUT_BASE/$run"
  if [[ -f "$out/default/fuzzer_stats" ]]; then
    printf '%s\n' "$out/default/fuzzer_stats"
  elif [[ -f "$out/fuzzer_stats" ]]; then
    printf '%s\n' "$out/fuzzer_stats"
  fi
}

stat_value() {
  local file=$1 key=$2
  awk -F: -v k="$key" '{gsub(/ /, "", $1); if ($1 == k) {gsub(/ /, "", $2); print $2}}' "$file" 2>/dev/null | tail -n 1
}

find_afl_pid() {
  local run=$1 out="$OUT_BASE/$run"
  ps -eo pid=,ppid=,psr=,stat=,comm=,cmd= \
    | awk -v out="$out" '$5 == "afl-fuzz" && index($0, out) {print $1; exit}'
}

cleanup_run() {
  local run=$1
  local out="$OUT_BASE/$run"
  local runtime="$RUN_BASE/$run"
  python3 - "$out" "$runtime" <<'PY'
import os, signal, sys, time
needles = [p for p in sys.argv[1:] if p]
self_pid = os.getpid()
parent_pid = os.getppid()
pids = []
for name in os.listdir('/proc'):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in {self_pid, parent_pid}:
        continue
    try:
        raw = open(f'/proc/{pid}/cmdline', 'rb').read()
    except OSError:
        continue
    cmd = raw.replace(b'\0', b' ').decode('utf-8', 'replace')
    if any(needle in cmd for needle in needles):
        pids.append(pid)
for sig in (signal.SIGTERM, signal.SIGKILL):
    for pid in list(pids):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    time.sleep(2)
print('cleanup_run_pids=' + ','.join(map(str, pids)))
PY
}

worker() {
  local run=$1 cpu=$2
  local out="$OUT_BASE/$run"
  local runtime="$RUN_BASE/$run"
  local logdir="$LOG_BASE/$run"
  local tmpdir="$runtime/tmp"
  mkdir -p "$out" "$runtime" "$tmpdir" "$logdir" "$STATE_DIR"
  exec >>"$logdir/worker.log" 2>&1
  echo "worker_start=$(date -Is)"
  echo "run=$run cpu=$cpu out=$out runtime=$runtime tmpdir=$tmpdir"
  echo $$ > "$STATE_DIR/$run.worker.pid"
  chown -R postgres:postgres "$out" "$runtime" "$logdir"
  trap 'cleanup_run "'$run'"' EXIT INT TERM
  cd "$ROOT"
  local start_epoch end_epoch
  start_epoch=$(date +%s)
  end_epoch=$((start_epoch + DURATION))
  {
    echo "run=$run"
    echo "cpu=$cpu"
    echo "worker_pid=$$"
    echo "output=$out"
    echo "runtime=$runtime"
    echo "log=$logdir/worker.log"
    echo "start_epoch=$start_epoch"
    echo "expected_end_epoch=$end_epoch"
    echo "start_time=$(date -Is -d @${start_epoch})"
    echo "expected_end_time=$(date -Is -d @${end_epoch})"
    echo "command=taskset -c $cpu runuser -u postgres --preserve-environment -- env TMPDIR=$tmpdir AFL_IGNORE_PROBLEMS=1 AFL_NO_FORKSRV=1 AFL_SKIP_BIN_CHECK=1 AFL_SKIP_CPUFREQ=1 AFL_NO_UI=1 $AFL -i $SEEDS -o $out -V $DURATION -t 120000 -m none -- python3 $TARGET --input @@ --runtime-root $runtime --log-root $logdir/postgresql --max-statements 20 --quiet"
  } > "$STATE_DIR/$run.meta"
  set +e
  timeout --kill-after=60s 86520 \
    taskset -c "$cpu" \
    runuser -u postgres --preserve-environment -- env \
      TMPDIR="$tmpdir" \
      AFL_IGNORE_PROBLEMS=1 \
      AFL_NO_FORKSRV=1 \
      AFL_SKIP_BIN_CHECK=1 \
      AFL_SKIP_CPUFREQ=1 \
      AFL_NO_UI=1 \
      "$AFL" \
        -i "$SEEDS" \
        -o "$out" \
        -V "$DURATION" \
        -t 120000 \
        -m none \
        -- python3 "$TARGET" \
          --input @@ \
          --runtime-root "$runtime" \
          --log-root "$logdir/postgresql" \
          --max-statements 20 \
          --quiet &
  local child=$!
  echo "$child" > "$STATE_DIR/$run.timeout.pid"
  wait "$child"
  local rc=$?
  set -e
  echo "worker_end=$(date -Is) rc=$rc"
  echo "$rc" > "$STATE_DIR/$run.exit"
  exit "$rc"
}

start_runs() {
  mkdir -p "$LOG_BASE" "$STATE_DIR"
  local existing=0
  for run in "${RUNS[@]}"; do
    if [[ -e "$OUT_BASE/$run" ]]; then
      echo "REFUSE: target output exists: $OUT_BASE/$run" >&2
      existing=1
    fi
  done
  if [[ "$existing" -ne 0 ]]; then
    echo "No task started because at least one target output already exists." >&2
    exit 20
  fi
  test -x "$AFL"
  test -x "$TARGET"
  local i run cpu session
  for i in "${!RUNS[@]}"; do
    run=${RUNS[$i]}
    cpu=${CPUS[$i]}
    session=$(session_name "$run")
    if tmux has-session -t "$session" 2>/dev/null; then
      echo "$run already has tmux session $session; skipping" >&2
      continue
    fi
    mkdir -p "$LOG_BASE/$run" "$RUN_BASE/$run" "$OUT_BASE/$run"
    chown -R postgres:postgres "$LOG_BASE/$run" "$RUN_BASE/$run" "$OUT_BASE/$run"
    if tmux new-session -d -s "$session" "bash '$SCRIPT' __worker '$run' '$cpu'"; then
      echo "STARTED $run session=$session cpu=$cpu output=$OUT_BASE/$run runtime=$RUN_BASE/$run log=$LOG_BASE/$run/worker.log"
    else
      echo "FAILED_TO_START $run session=$session" >&2
      cleanup_run "$run" || true
    fi
  done
}

status_runs() {
  printf 'run\tsession\tworker_pid\tafl_pid\tcpu\tpsr\taffinity\trun_time\texecs_done\tqueue\tfuzzer_stats\tplot_data\toutput\truntime\tlog\n'
  local i run cpu session worker afl stats run_time execs queue stats_ok plot psr affinity
  for i in "${!RUNS[@]}"; do
    run=${RUNS[$i]}
    cpu=${CPUS[$i]}
    session=$(session_name "$run")
    worker=$(cat "$STATE_DIR/$run.worker.pid" 2>/dev/null || true)
    afl=$(find_afl_pid "$run" || true)
    stats=$(stats_path "$run" || true)
    run_time="NA"; execs="NA"; stats_ok=NO; queue=NO; plot=NO; psr=NA; affinity=NA
    if [[ -n "$stats" ]]; then
      stats_ok=YES
      run_time=$(stat_value "$stats" run_time || true)
      execs=$(stat_value "$stats" execs_done || true)
    fi
    [[ -d "$OUT_BASE/$run/default/queue" || -d "$OUT_BASE/$run/queue" ]] && queue=YES
    [[ -s "$OUT_BASE/$run/default/plot_data" || -s "$OUT_BASE/$run/plot_data" ]] && plot=YES
    if [[ -n "$afl" ]]; then
      psr=$(ps -o psr= -p "$afl" 2>/dev/null | tr -d ' ' || true)
      affinity=$(taskset -pc "$afl" 2>/dev/null | awk -F: '{gsub(/^ /,"",$2); print $2}' || true)
    fi
    if tmux has-session -t "$session" 2>/dev/null; then session_state=alive; else session_state=dead; fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$run" "$session_state" "${worker:-NA}" "${afl:-NA}" "$cpu" "$psr" "${affinity:-NA}" "${run_time:-NA}" "${execs:-NA}" "$queue" "$stats_ok" "$plot" "$OUT_BASE/$run" "$RUN_BASE/$run" "$LOG_BASE/$run/worker.log"
  done
}

stop_runs() {
  local run session worker cmd
  for run in "${RUNS[@]}"; do
    session=$(session_name "$run")
    worker=$(cat "$STATE_DIR/$run.worker.pid" 2>/dev/null || true)
    if [[ -n "$worker" && -r "/proc/$worker/cmdline" ]]; then
      cmd=$(tr '\0' ' ' < "/proc/$worker/cmdline")
      if [[ "$cmd" == *"start_postgresql_5x24h.sh"* && "$cmd" == *"__worker $run"* ]]; then
        kill -TERM "$worker" 2>/dev/null || true
      fi
    fi
    if tmux has-session -t "$session" 2>/dev/null; then
      tmux kill-session -t "$session" || true
    fi
    cleanup_run "$run" || true
    echo "STOPPED $run"
  done
}

case "${1:-}" in
  start) start_runs ;;
  status) status_runs ;;
  stop) stop_runs ;;
  __worker) worker "$2" "$3" ;;
  *) echo "usage: $0 {start|status|stop}" >&2; exit 2 ;;
esac
