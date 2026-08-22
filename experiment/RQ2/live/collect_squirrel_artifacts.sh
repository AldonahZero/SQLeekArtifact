#!/usr/bin/env bash
set -euo pipefail

BASE=${BASE:-/root/SQLeek/experiment/RQ2}
OUT_ROOT=${OUT_ROOT:-$BASE/collected/squirrel}
LIVE_LOG_DIR=${LIVE_LOG_DIR:-$BASE/live/logs}
CUTOFF_MS=${CUTOFF_MS:-86400000}
CUTOFF_SEC=${CUTOFF_SEC:-86400}
PAUSE_AFTER=${PAUSE_AFTER:-0}
TS=${TS:-$(date -u +%Y%m%d_%H%M%S)}
OUT=$OUT_ROOT/collect_$TS
DEFAULT_DIR=/workspace/fuzzing/fuzz_out_dir/default

mkdir -p "$OUT/containers" "$OUT/notes"
CONTAINERS_FILE=$OUT/containers.txt
MANIFEST=$OUT/manifest.tsv
SUMMARY=$OUT/summary.tsv

if docker ps -a --format '{{.Names}}' | grep '^rq2_squirrel_' | sort > "$CONTAINERS_FILE"; then
  :
else
  : > "$CONTAINERS_FILE"
fi

printf 'container\timage\tstatus\tstarted_at\tcollected_at\trun_time_sec\tqueue_total\tqueue_24h\tcrashes_total\tcrashes_24h\thangs_total\thangs_24h\traw_tar\tcutoff_24h_tar\n' > "$MANIFEST"
printf 'container\tmetric\tvalue\n' > "$SUMMARY"

count_total() {
  local c=$1
  local d=$2
  docker exec "$c" sh -lc "cd '$DEFAULT_DIR' 2>/dev/null && find '$d' -maxdepth 1 -type f ! -name README.txt 2>/dev/null | wc -l" 2>/dev/null | tr -d ' '
}

count_cutoff() {
  local c=$1
  local d=$2
  docker exec "$c" sh -lc "cd '$DEFAULT_DIR' 2>/dev/null || exit 0; find '$d' -maxdepth 1 -type f ! -name README.txt 2>/dev/null | while IFS= read -r f; do b=\$(basename \"\$f\"); t=0; case \"\$b\" in *time:*) t=\${b#*time:}; t=\${t%%,*};; esac; [ \"\$t\" -le '$CUTOFF_MS' ] 2>/dev/null && printf '%s\\n' \"\$f\"; done | wc -l" 2>/dev/null | tr -d ' '
}

for c in $(cat "$CONTAINERS_FILE"); do
  RUN_DIR=$OUT/containers/$c
  mkdir -p "$RUN_DIR"

  image=$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)
  status=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || true)
  started_at=$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null || true)
  collected_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  docker inspect "$c" > "$RUN_DIR/docker_inspect.json" 2>/dev/null || true
  docker logs --timestamps "$c" > "$RUN_DIR/docker_logs.txt" 2>&1 || true
  if [ -f "$LIVE_LOG_DIR/$c.log" ]; then
    cp "$LIVE_LOG_DIR/$c.log" "$RUN_DIR/launch_log.txt" || true
  fi

  docker exec "$c" cat "$DEFAULT_DIR/fuzzer_stats" > "$RUN_DIR/fuzzer_stats" 2>/dev/null || true
  docker exec "$c" cat "$DEFAULT_DIR/plot_data" > "$RUN_DIR/plot_data" 2>/dev/null || true
  docker exec "$c" sh -lc "awk -F, -v cut='$CUTOFF_SEC' '/^#/ {print; next} (\$1+0)<=cut {print}' '$DEFAULT_DIR/plot_data'" > "$RUN_DIR/plot_data_24h" 2>/dev/null || true
  docker exec "$c" sh -lc "awk -F, -v cut='$CUTOFF_SEC' '/^#/ {next} (\$1+0)<=cut {last=\$0} END {if (last) print last}' '$DEFAULT_DIR/plot_data'" > "$RUN_DIR/plot_data_t24_last.csv" 2>/dev/null || true

  run_time=$(awk -F: '/^run_time/ {gsub(/ /,"",$2); print $2}' "$RUN_DIR/fuzzer_stats" 2>/dev/null || true)
  queue_total=$(count_total "$c" queue); queue_total=${queue_total:-0}
  queue_24h=$(count_cutoff "$c" queue); queue_24h=${queue_24h:-0}
  crashes_total=$(count_total "$c" crashes); crashes_total=${crashes_total:-0}
  crashes_24h=$(count_cutoff "$c" crashes); crashes_24h=${crashes_24h:-0}
  hangs_total=$(count_total "$c" hangs); hangs_total=${hangs_total:-0}
  hangs_24h=$(count_cutoff "$c" hangs); hangs_24h=${hangs_24h:-0}

  RAW_TAR=$RUN_DIR/raw_default.tar.gz
  CUTOFF_TAR=$RUN_DIR/cutoff_24h_default.tar.gz

  set +e
  docker cp "$c:$DEFAULT_DIR" - 2> "$RUN_DIR/raw_tar.stderr" | gzip -c > "$RAW_TAR"
  raw_rc=${PIPESTATUS[0]}
  docker exec "$c" sh -lc "cd '$DEFAULT_DIR' 2>/dev/null || exit 0; { [ -f fuzzer_stats ] && printf '%s\\n' fuzzer_stats; [ -f plot_data ] && printf '%s\\n' plot_data; find queue crashes hangs -maxdepth 1 -type f ! -name README.txt 2>/dev/null | while IFS= read -r f; do b=\$(basename \"\$f\"); t=0; case \"\$b\" in *time:*) t=\${b#*time:}; t=\${t%%,*};; esac; [ \"\$t\" -le '$CUTOFF_MS' ] 2>/dev/null && printf '%s\\n' \"\$f\"; done; } | tee /tmp/rq2_cutoff_files.txt | tar --ignore-failed-read --warning=no-file-changed -czf - -T -" > "$CUTOFF_TAR" 2> "$RUN_DIR/cutoff_tar.stderr"
  cutoff_rc=$?
  docker exec "$c" cat /tmp/rq2_cutoff_files.txt > "$RUN_DIR/cutoff_24h_file_list.txt" 2>/dev/null || true
  set -e

  printf '%s\traw_tar_exit\t%s\n' "$c" "$raw_rc" >> "$SUMMARY"
  printf '%s\tcutoff_tar_exit\t%s\n' "$c" "$cutoff_rc" >> "$SUMMARY"
  printf '%s\tqueue_total\t%s\n' "$c" "$queue_total" >> "$SUMMARY"
  printf '%s\tqueue_24h\t%s\n' "$c" "$queue_24h" >> "$SUMMARY"
  printf '%s\tcrashes_total\t%s\n' "$c" "$crashes_total" >> "$SUMMARY"
  printf '%s\tcrashes_24h\t%s\n' "$c" "$crashes_24h" >> "$SUMMARY"
  printf '%s\thangs_total\t%s\n' "$c" "$hangs_total" >> "$SUMMARY"
  printf '%s\thangs_24h\t%s\n' "$c" "$hangs_24h" >> "$SUMMARY"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$c" "$image" "$status" "$started_at" "$collected_at" "${run_time:-}" \
    "$queue_total" "$queue_24h" "$crashes_total" "$crashes_24h" "$hangs_total" "$hangs_24h" \
    "$RAW_TAR" "$CUTOFF_TAR" >> "$MANIFEST"
done

cat > "$OUT/README.md" <<EOF
# SQUIRREL RQ2 Collection $TS

This directory contains raw and 24h-cutoff artifacts copied from rq2_squirrel_* containers.

- raw_default.tar.gz: full AFL default output directory copied with docker cp.
- cutoff_24h_default.tar.gz: queue/crashes/hangs files whose AFL filename time is <= ${CUTOFF_MS} ms, plus fuzzer_stats and plot_data.
- plot_data_24h: plot_data rows with elapsed seconds <= ${CUTOFF_SEC}.
- plot_data_t24_last.csv: last plot_data row at or before 24h.
- docker_inspect.json, docker_logs.txt, launch_log.txt: run context.

Main RQ2 replay should use cutoff_24h_default.tar.gz. The raw tar is retained for audit and debugging.
EOF

if [ "$PAUSE_AFTER" = "1" ]; then
  docker ps --format '{{.Names}}' | grep '^rq2_squirrel_' | xargs -r docker pause
fi

echo "$OUT"