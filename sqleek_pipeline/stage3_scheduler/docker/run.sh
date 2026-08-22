#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$STAGE_DIR/../.." && pwd)"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./docker/run.sh <mysql|mariadb|postgresql|monetdb> <run_id> <duration> <seed_dir> <target_dir> <output_root>

Environment:
  SQLEEK_IMAGE_TAG   Image tag, default current SQLeek git short SHA
  CPUSET_CPUS        Docker --cpuset-cpus value
  CONTAINER_MEMORY   Docker --memory value, default 16g
  MODE               run or smoke, default run
  SQLRIGHT_INPUT_LIMIT  Limit SQLRight active input files, default all
  STAGED_CORPUS_ENABLED Enable staged corpus sync importer, default 0
  STAGED_BATCH_SIZE     Staged corpus batch size, default 100
  STAGED_BATCH_INTERVAL Seconds between staged batches, default 60
  STAGED_DRY_RUN_TIMEOUT Seconds to wait for initial dry run, default 3600
  STAGED_SYNC_ID        AFL sync sibling id for deferred corpus, default sqleek_staged
EOF
}

[ $# -eq 6 ] || { usage; exit 2; }
dbms="$1"
run_id="$2"
duration="$3"
seed_dir="$4"
target_dir="$5"
output_root="$6"

case "$dbms" in
  postgres) dbms="postgresql" ;;
  mysql|mariadb|postgresql|monetdb) ;;
  *) usage; exit 2 ;;
esac

[ -d "$seed_dir" ] || { echo "missing seed dir: $seed_dir" >&2; exit 2; }
find "$seed_dir" -type f -size +0c -print -quit | grep -q . || { echo "seed dir has no non-empty files: $seed_dir" >&2; exit 2; }
[ -d "$target_dir" ] || { echo "missing target dir: $target_dir" >&2; exit 2; }

tag="${SQLEEK_IMAGE_TAG:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d)}"
image="sqleek-${dbms}:${tag}"
docker image inspect "$image" >/dev/null

run_root="${output_root%/}/${dbms}/${run_id}"
if [ -e "$run_root" ]; then
  echo "refusing to overwrite existing run: $run_root" >&2
  exit 3
fi
mkdir -p "$run_root/output" "$run_root/logs" "$run_root/runtime" "$run_root/meta"

container="sqleek_stage3_${dbms}_${run_id}"
if docker ps -a --format '{{.Names}}' | grep -Fxq "$container"; then
  echo "container already exists: $container" >&2
  exit 3
fi

image_id="$(docker image inspect "$image" --format '{{.Id}}')"
image_repo_digest="$(docker image inspect "$image" --format '{{join .RepoDigests "\n"}}' || true)"
cp "$SCRIPT_DIR/versions.lock" "$run_root/meta/versions.lock"
[ -n "${SCHEDULER_CONFIG:-}" ] && cp "$SCHEDULER_CONFIG" "$run_root/meta/scheduler_config.json"
printf '%s\n' "$image" > "$run_root/meta/image.txt"
printf '%s\n' "$image_id" > "$run_root/meta/image_id.txt"
printf '%s\n' "$image_repo_digest" > "$run_root/meta/image_repo_digest.txt"

seed_dir_real="$(readlink -f "$seed_dir")"
target_dir_real="$(readlink -f "$target_dir")"
run_output_real="$(readlink -f "$run_root/output")"
run_logs_real="$(readlink -f "$run_root/logs")"
run_runtime_real="$(readlink -f "$run_root/runtime")"
printf '%s\n' "$seed_dir_real" > "$run_root/meta/seed_dir.txt"
find "$seed_dir_real" -maxdepth 1 -type f -size +0c -printf '%f\t%s\n' | sort > "$run_root/meta/seed_files.tsv"
seed_count="$(wc -l < "$run_root/meta/seed_files.tsv")"
printf '%s\n' "$seed_count" > "$run_root/meta/seed_count.txt"
corpus_manifest="${CORPUS_MANIFEST:-}"
if [ -z "$corpus_manifest" ] && [ -f "$seed_dir_real/../manifest.jsonl" ]; then
  corpus_manifest="$seed_dir_real/../manifest.jsonl"
fi
if [ -n "$corpus_manifest" ]; then
  corpus_manifest="$(readlink -f "$corpus_manifest")"
  sha256sum "$corpus_manifest" > "$run_root/meta/corpus_manifest.sha256"
  cp "$corpus_manifest" "$run_root/meta/corpus_manifest.jsonl"
  [ -f "$(dirname "$corpus_manifest")/summary.json" ] && cp "$(dirname "$corpus_manifest")/summary.json" "$run_root/meta/corpus_summary.json"
fi

cmd=(
  docker run -d
  --name "$container"
  --label org.sqleek.stage=stage3
  --label org.sqleek.dbms="$dbms"
  --label org.sqleek.run_id="$run_id"
  --memory "${CONTAINER_MEMORY:-16g}"
  -e DBMS="$dbms"
  -e RUN_ID="$run_id"
  -e DURATION="$duration"
  -e AFL_TIMEOUT="${AFL_TIMEOUT:-1000}"
  -e MEMORY_LIMIT="${MEMORY_LIMIT:-4096}"
  -e AFL_TMPDIR="${AFL_TMPDIR:-}"
  -e TMPDIR="${TMPDIR:-}"
  -e CPU_ID="${CPU_ID:-}"
  -e SCHEDULER_CONFIG="${SCHEDULER_CONFIG:-}"
  -e AFL_EXTRA_ARGS="${AFL_EXTRA_ARGS:-}"
  -e AFL_IGNORE_SEED_PROBLEMS="${AFL_IGNORE_SEED_PROBLEMS:-}"
  -e SQLEEK_PG_LOG_MIN_MESSAGES="${SQLEEK_PG_LOG_MIN_MESSAGES:-}"
  -e SQLEEK_PG_CLIENT_MIN_MESSAGES="${SQLEEK_PG_CLIENT_MIN_MESSAGES:-}"
  -e SQLEEK_PG_LOG_ERROR_VERBOSITY="${SQLEEK_PG_LOG_ERROR_VERBOSITY:-}"
  -e SQLEEK_PG_SUPPRESS_SERVER_LOG="${SQLEEK_PG_SUPPRESS_SERVER_LOG:-}"
  -e SQLEEK_PG_CONNECT_RETRIES="${SQLEEK_PG_CONNECT_RETRIES:-}"
  -e SQLEEK_PG_STATEMENT_TIMEOUT_MS="${SQLEEK_PG_STATEMENT_TIMEOUT_MS:-}"
  -e SQLEEK_PG_CLIENT_TIMEOUT_MS="${SQLEEK_PG_CLIENT_TIMEOUT_MS:-}"
  -e SQLRIGHT_INPUT_LIMIT="${SQLRIGHT_INPUT_LIMIT:-all}"
  -e STAGED_CORPUS_ENABLED="${STAGED_CORPUS_ENABLED:-0}"
  -e STAGED_BATCH_SIZE="${STAGED_BATCH_SIZE:-100}"
  -e STAGED_BATCH_INTERVAL="${STAGED_BATCH_INTERVAL:-60}"
  -e STAGED_DRY_RUN_TIMEOUT="${STAGED_DRY_RUN_TIMEOUT:-3600}"
  -e STAGED_QUEUE_WAIT_TIMEOUT="${STAGED_QUEUE_WAIT_TIMEOUT:-300}"
  -e STAGED_SYNC_ID="${STAGED_SYNC_ID:-sqleek_staged}"
  -v "$seed_dir_real:/workspace/seeds:ro"
  -v "$target_dir_real:/workspace/targets:ro"
  -v "$run_output_real:/workspace/output"
  -v "$run_logs_real:/workspace/logs"
  -v "$run_runtime_real:/workspace/runtime"
)

if [ -n "${CPUSET_CPUS:-}" ]; then
  cmd+=(--cpuset-cpus "$CPUSET_CPUS")
fi

if [ "$dbms" = "postgresql" ] && [ -n "${SQLEEK_POSTGRES_RUN_PARALLEL_OVERRIDE:-}" ]; then
  pg_override_real="$(readlink -f "$SQLEEK_POSTGRES_RUN_PARALLEL_OVERRIDE")"
  [ -f "$pg_override_real" ] || { echo "missing postgres run_parallel override: $SQLEEK_POSTGRES_RUN_PARALLEL_OVERRIDE" >&2; exit 2; }
  cmd+=( -v "$pg_override_real:/opt/sqlright/PostgreSQL/docker/fuzz_root/run_parallel.py:ro" )
fi

if [ "$dbms" = "monetdb" ]; then
  scheduler_script_real="$(readlink -f "$SCRIPT_DIR/common/run_scheduler.sh")"
  [ -f "$scheduler_script_real" ] || { echo "missing scheduler script: $scheduler_script_real" >&2; exit 2; }
  cmd+=( -v "$scheduler_script_real:/opt/sqleek/stage3_scheduler/docker/common/run_scheduler.sh:ro" )
  sha256sum "$scheduler_script_real" > "$run_root/meta/run_scheduler.sh.sha256"
fi

cmd+=("$image" "${MODE:-run}")
printf '%q ' "${cmd[@]}" > "$run_root/meta/docker_command.sh"
printf '\n' >> "$run_root/meta/docker_command.sh"
"${cmd[@]}"
echo "$container" > "$run_root/meta/container.txt"
echo "started $container"
