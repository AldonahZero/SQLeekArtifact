#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COLLECTION=/root/SQLeek/experiment/RQ2/collected/squirrel/collect_20260616_090502
OUT_ROOT=/root/SQLeek/experiment/RQ2/replay/output
CHECKPOINTS_MIN=60,180,300,480,600,720,900,1200,1440
SEED_TIMEOUT=60
DOCKER_MEM=${RQ2_REPLAY_DOCKER_MEM:-120G}
DOCKER_SHM=${RQ2_REPLAY_DOCKER_SHM:-4G}
IMAGE=${RQ2_POSTGRES_IMAGE:-griffin_postgres_llvmcov}
BINARY=${RQ2_POSTGRES_BINARY:-/root/bin_aflpp/usr/local/pgsql/bin/postgres}
TOOL=SQUIRREL
DBMS=postgres

usage() {
  cat <<'USAGE'
Usage: rq2_replay_squirrel_postgres_bucketed.sh [options]

Options:
  --collection DIR       SQUIRREL collection dir with manifest.tsv
  --out-root DIR         Output root directory
  --checkpoints-min LIST Comma-separated checkpoints, default 60,180,300,480,600,720,900,1200,1440
  --seed-timeout SEC     Timeout for each SQL seed, default 60

This is optimized for the RQ2 24h curve: each repeat replays its seed stream
once in discovery-time order, snapshots cumulative LLVM coverage at each
checkpoint, summarizes CSVs, then deletes large intermediate cov.json files.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --collection) COLLECTION="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --checkpoints-min) CHECKPOINTS_MIN="$2"; shift 2 ;;
    --seed-timeout) SEED_TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

TS=$(date -u +%Y%m%d_%H%M%S)
OUT="$OUT_ROOT/bucketed_squirrel_postgres_${TS}"
mkdir -p "$OUT"/{work,run_data,data}

IFS=',' read -r -a CHECKPOINT_ARR <<< "$CHECKPOINTS_MIN"
CHECKPOINTS_MS=()
for cp in "${CHECKPOINT_ARR[@]}"; do
  CHECKPOINTS_MS+=("$((cp * 60 * 1000))")
done
CHECKPOINTS_MS_CSV=$(IFS=','; echo "${CHECKPOINTS_MS[*]}")

python3 "$SCRIPT_DIR/build_target_regions.py" --dbms postgres --out "$OUT/target_regions.csv"

PREFLIGHT="$OUT/preflight_status.tsv"
MASTER_INDEX="$OUT/replay_index.tsv"
printf 'dbms\timage\tbinary\tstatus\tmessage\n' > "$PREFLIGHT"
printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$MASTER_INDEX"

if docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "command -v llvm-profdata-12 >/dev/null && command -v llvm-cov-12 >/dev/null && test -x '$BINARY' && strings '$BINARY' | grep -q __llvm_prf"; then
  printf 'postgres\t%s\t%s\tok\tLLVM source coverage backend ready\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
else
  printf 'postgres\t%s\t%s\tmissing\tLLVM source coverage backend not usable\n' "$IMAGE" "$BINARY" >> "$PREFLIGHT"
  echo "$OUT"
  exit 1
fi

manifest="$COLLECTION/manifest.tsv"
if [[ ! -f "$manifest" ]]; then
  echo "missing manifest: $manifest" >&2
  exit 1
fi

append_csv() {
  local src="$1" dest="$2"
  if [[ ! -s "$src" ]]; then
    return
  fi
  if [[ ! -f "$dest" ]]; then
    cp "$src" "$dest"
  else
    tail -n +2 "$src" >> "$dest"
  fi
}

while IFS=$'\t' read -r container image _status started_at _collected_at _run_time _queue_total _queue_24h _crashes_total _crashes_24h _hangs_total _hangs_24h _raw_tar cutoff_tar; do
  [[ "$container" == container ]] && continue
  if [[ ! "$container" =~ ^rq2_squirrel_postgres_r([0-9]+)_ ]]; then
    continue
  fi
  repeat="${BASH_REMATCH[1]}"
  run_id="postgres_squirrel_r${repeat}"
  run_work="$OUT/work/$run_id"
  queue_dir="$run_work/queue"
  mkdir -p "$run_work/extract" "$run_work/out"
  if [[ ! -d "$queue_dir" ]]; then
    tar -xzf "$cutoff_tar" -C "$run_work/extract" --wildcards 'queue/*'
    mv "$run_work/extract/queue" "$queue_dir"
  fi
  seed_total=$(find "$queue_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  name="rq2_bucketed_postgres_r${repeat}_${TS}_${RANDOM}"
  prefix="/rq2_out/${run_id}"
  host_prefix="$run_work/out/${run_id}"
  echo "[$(date -Is)] start $run_id seed_total=$seed_total"
  if docker run --rm --privileged -m "$DOCKER_MEM" --shm-size="$DOCKER_SHM" \
      -e GRIFFIN_CONTAINER=1 \
      -e LLVM_PROFILE_FILE='%c/tmp/rq2_prof/%p-%m.profraw' \
      -v "$SCRIPT_DIR":/rq2_scripts:ro \
      -v "$queue_dir":/rq2_queue:ro \
      -v "$run_work/out":/rq2_out \
      --name "$name" --entrypoint /bin/bash "$IMAGE" \
      /rq2_scripts/container_replay_llvm_bucketed.sh \
        --dbms postgres --binary "$BINARY" --checkpoints-ms "$CHECKPOINTS_MS_CSV" \
        --seed-timeout "$SEED_TIMEOUT" --process-name pg_c_8888 --out-prefix "$prefix"; then
    status=complete
    message=
  else
    status=failed
    message="docker bucketed replay failed; inspect $run_work/out"
  fi
  end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  RUN_INDEX="$run_work/replay_index.tsv"
  printf 'run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\tcontainer_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n' > "$RUN_INDEX"
  for cp in "${CHECKPOINT_ARR[@]}"; do
    cov_json="${host_prefix}_t${cp}.cov.json"
    report_txt="${host_prefix}_t${cp}.report.txt"
    meta="${host_prefix}_t${cp}.meta.tsv"
    cp_seed_count="$seed_total"
    if [[ -f "$meta" ]]; then
      cp_seed_count=$(awk -F'\t' '$1=="seed_count"{print $2}' "$meta")
    fi
    row_status="$status"
    row_message="$message"
    if [[ "$status" == complete && ! -f "$cov_json" ]]; then
      row_status=failed
      row_message="missing cov_json for checkpoint $cp"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t\t%s\t%s\n' \
      "$run_id" "$TOOL" "$DBMS" "$repeat" "$cp" "$cov_json" "$report_txt" "$row_status" "$row_message" \
      "$IMAGE" "$BINARY" "$cp_seed_count" "$cutoff_tar" "$IMAGE" "$container" "$start_time" "$end_time" >> "$RUN_INDEX"
  done
  tail -n +2 "$RUN_INDEX" >> "$MASTER_INDEX"

  python3 "$SCRIPT_DIR/summarize_llvm_cov.py" \
    --target-regions "$OUT/target_regions.csv" \
    --replay-index "$RUN_INDEX" \
    --out "$OUT/run_data/$run_id"

  append_csv "$OUT/run_data/$run_id/runs.csv" "$OUT/data/runs.csv"
  append_csv "$OUT/run_data/$run_id/coverage_summary.csv" "$OUT/data/coverage_summary.csv"
  append_csv "$OUT/run_data/$run_id/coverage_timeseries.csv" "$OUT/data/coverage_timeseries.nozero.csv"
  append_csv "$OUT/run_data/$run_id/target_region_hits.csv" "$OUT/data/target_region_hits.csv"
  append_csv "$OUT/run_data/$run_id/target_branch_hits.csv" "$OUT/data/target_branch_hits.csv"
  append_csv "$OUT/run_data/$run_id/component_heatmap_by_run.csv" "$OUT/data/component_heatmap_by_run.csv"

  find "$run_work/out" -name '*.cov.json' -type f -printf '%p\t%s\n' >> "$OUT/deleted_cov_json.tsv" || true
  find "$run_work/out" -name '*.cov.json' -type f -delete || true
  echo "[$(date -Is)] done $run_id"
done < "$manifest"

python3 - "$OUT" <<'PY'
from __future__ import annotations
import csv
import pathlib
import sys
from collections import defaultdict

out = pathlib.Path(sys.argv[1])
data = out / "data"

def read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as fp:
        return list(csv.DictReader(fp))

def write_csv(path: pathlib.Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})

ts_fields = [
    "run_id", "tool", "dbms", "repeat_id", "elapsed_min",
    "risk_branches_hit", "target_region_branch_coverage",
    "risk_targets_hit", "target_function_hit_rate",
    "global_branches_hit", "global_branch_coverage",
]
ts_rows = read_csv(data / "coverage_timeseries.nozero.csv")
run_rows = read_csv(data / "runs.csv")
for rr in run_rows:
    ts_rows.append({
        "run_id": rr["run_id"],
        "tool": rr["tool"],
        "dbms": rr["dbms"],
        "repeat_id": rr["repeat_id"],
        "elapsed_min": "0",
        "risk_branches_hit": "0",
        "target_region_branch_coverage": "0.0",
        "risk_targets_hit": "0",
        "target_function_hit_rate": "0.0",
        "global_branches_hit": "0",
        "global_branch_coverage": "0.0",
    })
ts_rows.sort(key=lambda r: (r["dbms"], int(r["repeat_id"]), int(r["elapsed_min"])))
write_csv(data / "coverage_timeseries.csv", ts_rows, ts_fields)

with (data / "runtime_edges_found.csv").open("w", newline="") as fp:
    fields = ["run_id", "tool", "dbms", "repeat_id", "relative_time", "edges_found"]
    w = csv.DictWriter(fp, fieldnames=fields)
    w.writeheader()
    for r in ts_rows:
        w.writerow({
            "run_id": r["run_id"],
            "tool": r["tool"],
            "dbms": r["dbms"],
            "repeat_id": r["repeat_id"],
            "relative_time": int(r["elapsed_min"]) * 60,
            "edges_found": r["global_branches_hit"],
        })

comp_rows = read_csv(data / "component_heatmap_by_run.csv")
groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
for row in comp_rows:
    groups[(row["dbms"], row["component"], row["tool"])].append(row)
agg = []
for (dbms, component, tool), rows in sorted(groups.items()):
    total = max(int(float(r["risk_branches_total"])) for r in rows) if rows else 0
    hit = sum(float(r["risk_branches_hit"]) for r in rows) / len(rows) if rows else 0.0
    targets_total = max(int(float(r["risk_targets_total"])) for r in rows) if rows else 0
    targets_hit = sum(float(r["risk_targets_hit"]) for r in rows) / len(rows) if rows else 0.0
    fn_rate = sum(float(r["target_function_hit_rate"]) for r in rows) / len(rows) if rows else 0.0
    agg.append({
        "dbms": dbms,
        "component": component,
        "tool": tool,
        "risk_branches_total": total,
        "risk_branches_hit": round(hit, 3),
        "target_region_branch_coverage": hit / total if total else 0.0,
        "risk_targets_total": targets_total,
        "risk_targets_hit": round(targets_hit, 3),
        "target_function_hit_rate": fn_rate,
    })
write_csv(
    data / "component_heatmap.csv",
    agg,
    [
        "dbms", "component", "tool",
        "risk_branches_total", "risk_branches_hit", "target_region_branch_coverage",
        "risk_targets_total", "risk_targets_hit", "target_function_hit_rate",
    ],
)
print(data)
PY

rm -f "$OUT/data/coverage_timeseries.nozero.csv"
echo "$OUT"
