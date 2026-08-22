#!/usr/bin/env python3
"""Replay SQLancer SQLite logs in statement order and export RQ2 coverage.

SQLancer logs are not AFL queues: they do not provide per-seed discovery
timestamps, and a whole 24h log can contain tens of thousands of SQL statements.
This driver therefore replays small ordered batches into one persistent SQLite
database per repeat, assigns checkpoint time by statement order, and merges the
profiles observed up to each checkpoint.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/root/SQLeek/experiment/RQ2")
REPLAY_DIR = BASE / "replay"
LIVE_SQLANCER = BASE / "live" / "sqlancer"
CHECKPOINTS = [60, 180, 300, 480, 600, 720, 900, 1200, 1440]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-root", type=Path, default=BASE / "replay" / "output")
    p.add_argument("--repeats", default="1,2")
    p.add_argument("--checkpoint-mins", default=",".join(str(x) for x in CHECKPOINTS))
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--max-batches", type=int, default=0, help="smoke-test cap; 0 means all batches")
    p.add_argument("--seed-timeout", type=int, default=3)
    p.add_argument("--docker-cpus", default="1")
    p.add_argument("--docker-mem", default="8G")
    p.add_argument("--image", default="griffin_sqlite_llvmcov")
    p.add_argument("--binary", default="/root/bin_aflpp/usr/local/bin/sqlite3")
    p.add_argument("--llvm-profdata", default="llvm-profdata-12")
    p.add_argument("--llvm-cov", default="llvm-cov-12")
    p.add_argument("--tool", default="SQLancer")
    p.add_argument("--apply-result", action="store_true")
    return p.parse_args()


def run(cmd: list[str], *, cwd: Path | None = None, stdout=None) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=stdout, check=True)


def split_csv_ints(text: str) -> list[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def find_run_dir(repeat: int) -> Path:
    matches = sorted(LIVE_SQLANCER.glob(f"rq2_sqlancer_sqlite_r{repeat}_*"))
    matches = [p for p in matches if p.is_dir()]
    if not matches:
        raise SystemExit(f"missing SQLancer SQLite live directory for repeat {repeat}")
    return matches[-1]


def find_log(run_dir: Path) -> Path:
    logs = sorted((run_dir / "logs" / "sqlite3").glob("*-cur.log"))
    if not logs:
        raise SystemExit(f"missing SQLancer SQLite log under {run_dir}")
    return logs[0]


def clean_sql_line(raw: str) -> str | None:
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("--"):
        return None
    line = re.sub(r"\s+--\s+\d+ms;\s*$", "", line.rstrip())
    line = line.replace("\\n", "\n").strip()
    if not line:
        return None
    return line


def read_statements(log_path: Path) -> list[str]:
    statements: list[str] = []
    with log_path.open(errors="replace") as fp:
        for raw in fp:
            stmt = clean_sql_line(raw)
            if stmt is None:
                continue
            statements.append(stmt)
    return statements


def batch_name(batch_id: int, time_ms: int) -> str:
    return f"id:{batch_id:06d},time:{time_ms},src:sqlancer,op:batch"


def write_batches(statements: list[str], out_dir: Path, batch_size: int, max_batches: int) -> list[dict[str, object]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("id:*"):
        old.unlink()
    rows: list[dict[str, object]] = []
    total = len(statements)
    if total == 0:
        raise SystemExit("SQLancer log contains no executable SQL statements")
    batch_count = (total + batch_size - 1) // batch_size
    if max_batches > 0:
        batch_count = min(batch_count, max_batches)
    for idx in range(batch_count):
        start = idx * batch_size
        end = min(total, start + batch_size)
        time_ms = int(round((end / total) * 24 * 60 * 60 * 1000))
        bid = f"{idx + 1:06d}"
        path = out_dir / batch_name(idx + 1, time_ms)
        payload = [".timeout 1000", ".bail off"]
        payload.extend(statements[start:end])
        payload.append("COMMIT;")
        path.write_text("\n".join(payload) + "\n", encoding="utf-8")
        rows.append(
            {
                "batch_id": bid,
                "time_ms": time_ms,
                "statement_start": start + 1,
                "statement_end": end,
                "seed_path": str(path),
            }
        )
    manifest = out_dir / "manifest.tsv"
    with manifest.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["batch_id", "time_ms", "statement_start", "statement_end", "seed_path"],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    return rows


CONTAINER_SCRIPT = r"""
set -euo pipefail
PROFILE_DIR=/tmp/rq2_prof
OUT=/rq2_out
BATCH_DIR=/rq2_batches
BINARY="${RQ2_SQLITE_BINARY}"
LLVM_PROFDATA_BIN="${RQ2_LLVM_PROFDATA}"
LLVM_COV_BIN="${RQ2_LLVM_COV}"
SEED_TIMEOUT="${RQ2_SEED_TIMEOUT}"
CHECKPOINTS="${RQ2_CHECKPOINTS}"

rm -rf "$PROFILE_DIR"
mkdir -p "$PROFILE_DIR" "$OUT"
db=/tmp/rq2_sqlancer_sqlite.db
rm -f "$db" "$db-journal" "$db-wal" "$db-shm"
: > "$OUT/replay.stdout"
: > "$OUT/replay.stderr"
printf 'batch_id\ttime_ms\texit_code\tprofile_count\n' > "$OUT/batch_execution.tsv"

while IFS=$'\t' read -r batch_id time_ms statement_start statement_end seed_path; do
  if [[ "$batch_id" == "batch_id" ]]; then
    continue
  fi
  batch_id="${batch_id//$'\r'/}"
  time_ms="${time_ms//$'\r'/}"
  seed_path="${seed_path//$'\r'/}"
  seed="$BATCH_DIR/$(basename "$seed_path")"
  export LLVM_PROFILE_FILE="%c${PROFILE_DIR}/b${batch_id}-%p-%m.profraw"
  set +e
  timeout "$SEED_TIMEOUT" "$BINARY" "$db" < "$seed" >> "$OUT/replay.stdout" 2>> "$OUT/replay.stderr"
  rc=$?
  set -e
  profile_count=$(find "$PROFILE_DIR" -maxdepth 1 -type f -name "b${batch_id}-*.profraw" | wc -l | tr -d ' ')
  printf '%s\t%s\t%s\t%s\n' "$batch_id" "$time_ms" "$rc" "$profile_count" >> "$OUT/batch_execution.tsv"
done < "$BATCH_DIR/manifest.tsv"

IFS=',' read -r -a cp_arr <<< "$CHECKPOINTS"
for cp in "${cp_arr[@]}"; do
  cp_ms=$((cp * 60 * 1000))
  list="$OUT/t${cp}.profraw.list"
  python3 - "$PROFILE_DIR" "$BATCH_DIR/manifest.tsv" "$cp_ms" "$list" <<'PY'
from __future__ import annotations
import csv
import glob
import sys
from pathlib import Path

profile_dir = Path(sys.argv[1])
manifest = Path(sys.argv[2])
checkpoint_ms = int(sys.argv[3])
out = Path(sys.argv[4])
profiles = []
with manifest.open(newline="", encoding="utf-8") as fp:
    for row in csv.DictReader(fp, delimiter="\t"):
        if int(row["time_ms"]) <= checkpoint_ms:
            profiles.extend(glob.glob(str(profile_dir / f"b{row['batch_id']}-*.profraw")))
profiles = sorted(set(profiles))
out.write_text("\n".join(profiles) + ("\n" if profiles else ""), encoding="utf-8")
PY
  if [[ ! -s "$list" ]]; then
    echo "no profiles for checkpoint $cp" >&2
    exit 5
  fi
  "$LLVM_PROFDATA_BIN" merge -sparse -f "$list" -o "$OUT/t${cp}.profdata"
  "$LLVM_COV_BIN" export -format=text "$BINARY" -instr-profile="$OUT/t${cp}.profdata" > "$OUT/t${cp}.cov.json"
  "$LLVM_COV_BIN" report "$BINARY" -instr-profile="$OUT/t${cp}.profdata" > "$OUT/t${cp}.report.txt"
done
"""


def docker_replay_repeat(
    *,
    args: argparse.Namespace,
    repeat: int,
    batch_dir: Path,
    replay_dir: Path,
    checkpoints: list[int],
) -> bool:
    replay_dir.mkdir(parents=True, exist_ok=True)
    name = f"rq2_sqlancer_sqlite_stmt_r{repeat}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        str(args.docker_cpus),
        "-m",
        str(args.docker_mem),
        "--shm-size",
        "1G",
        "-e",
        f"RQ2_SQLITE_BINARY={args.binary}",
        "-e",
        f"RQ2_LLVM_PROFDATA={args.llvm_profdata}",
        "-e",
        f"RQ2_LLVM_COV={args.llvm_cov}",
        "-e",
        f"RQ2_SEED_TIMEOUT={args.seed_timeout}",
        "-e",
        "RQ2_CHECKPOINTS=" + ",".join(str(x) for x in checkpoints),
        "-v",
        f"{batch_dir}:/rq2_batches:ro",
        "-v",
        f"{replay_dir}:/rq2_out",
        "--name",
        name,
        "--entrypoint",
        "/bin/bash",
        args.image,
        "-lc",
        CONTAINER_SCRIPT,
    ]
    print("+ " + " ".join(cmd[: cmd.index(args.image) + 1]) + " ...", flush=True)
    proc = subprocess.run(cmd)
    return proc.returncode == 0


def write_replay_index_header(path: Path) -> None:
    with path.open("w", encoding="utf-8") as fp:
        fp.write(
            "run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\tstatus\tmessage\t"
            "container_image\tbinary\tseed_count\tseed_corpus\tbuild_id\tcontainer_id\tversion\tstart_time\tend_time\n"
        )


def append_index_row(path: Path, row: dict[str, object]) -> None:
    fields = [
        "run_id",
        "tool",
        "dbms",
        "repeat_id",
        "checkpoint_min",
        "cov_json",
        "report_txt",
        "status",
        "message",
        "container_image",
        "binary",
        "seed_count",
        "seed_corpus",
        "build_id",
        "container_id",
        "version",
        "start_time",
        "end_time",
    ]
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\t".join(str(row.get(field, "")) for field in fields) + "\n")


def refresh_extra_figures() -> None:
    extras = [
        BASE / "scripts" / "plot_target_branch_region_over_time.py",
        BASE / "scripts" / "plot_mysql_sqleek_component_heatmap.py",
        BASE / "scripts" / "plot_mariadb_sqleek_component_heatmap.py",
    ]
    for script in extras:
        if script.exists():
            run(["python3", str(script)])


def read_result_sources() -> list[str]:
    readme = BASE / "result" / "README.md"
    sources: list[str] = []
    if readme.exists():
        for line in readme.read_text(errors="replace").splitlines():
            m = re.match(r"- `(/root/SQLeek/.+)`", line)
            if m:
                sources.append(m.group(1))
    return sources


def update_readme(out: Path, data_dir: Path, repeats: list[int], note: str, existing_sources: list[str]) -> None:
    readme = BASE / "result" / "README.md"
    for src in [str(data_dir)]:
        if src not in existing_sources:
            existing_sources.append(src)
    text = [
        "# RQ2 Result Package",
        "",
        "This directory contains real RQ2 campaign and replay results.",
        "Validated rows are merged one tool/DBMS pair at a time.",
        "",
        "Current real replacement sources:",
        "",
    ]
    text.extend(f"- `{src}`" for src in existing_sources)
    text.extend(
        [
            "",
            "- Pending pairs are kept as metadata-only runs and omitted from coverage tables/24h curves: SQUIRREL/MonetDB, SQLancer/MonetDB, SQLaser/MariaDB, and SQLaser/MonetDB. SQUIRREL and SQLancer MonetDB adapters are now checked in under experiment/RQ2; campaign results remain TODO.",
            "",
            "SQLancer/SQLite replay note:",
            "",
            f"- Source: `{out}`",
            f"- Repeats: {','.join(str(x) for x in repeats)}",
            f"- {note}",
            "",
            "Files under `data/` are the canonical plotting/table inputs. Files under `audit/<tool>_<dbms>/` preserve real target-region and target-branch audit CSVs.",
            "",
        ]
    )
    readme.write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    args = parse_args()
    repeats = split_csv_ints(args.repeats)
    checkpoints = split_csv_ints(args.checkpoint_mins)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = args.out_root / f"sqlancer_sqlite_statement_replay_{ts}"
    real = out / "real_sqlancer_sqlite"
    work = out / "work"
    out.mkdir(parents=True, exist_ok=True)
    real.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    target_regions = real / "target_regions.csv"
    run(["python3", str(REPLAY_DIR / "build_target_regions.py"), "--dbms", "sqlite", "--out", str(target_regions)])

    preflight = out / "preflight_status.tsv"
    with preflight.open("w", encoding="utf-8") as fp:
        fp.write("image\tbinary\tstatus\tmessage\n")
    try:
        run(["docker", "image", "inspect", args.image], stdout=subprocess.DEVNULL)
        run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "/bin/bash",
                args.image,
                "-lc",
                f"command -v {args.llvm_profdata} >/dev/null && command -v {args.llvm_cov} >/dev/null && test -x {args.binary} && strings {args.binary} | grep -q __llvm_prf",
            ],
            stdout=subprocess.DEVNULL,
        )
        with preflight.open("a", encoding="utf-8") as fp:
            fp.write(f"{args.image}\t{args.binary}\tok\tLLVM source coverage backend ready\n")
    except subprocess.CalledProcessError:
        with preflight.open("a", encoding="utf-8") as fp:
            fp.write(f"{args.image}\t{args.binary}\tfailed\tcoverage backend preflight failed\n")
        raise

    sqlite_map = out / "source_map" / "sqlite3_llvmcov.c"
    sqlite_map.parent.mkdir(parents=True, exist_ok=True)
    with (sqlite_map.with_suffix(".tmp")).open("wb") as fp:
        run(["docker", "run", "--rm", args.image, "cat", "/root/bld_llvmcov/sqlite3.c"], stdout=fp)
    sqlite_map.with_suffix(".tmp").replace(sqlite_map)

    index = real / "replay_index.tsv"
    write_replay_index_header(index)
    note = "Checkpoint times use SQL statement-order approximation because SQLancer logs do not record per-statement wall-clock discovery times."
    (out / "README.md").write_text(note + "\n", encoding="utf-8")

    for repeat in repeats:
        run_dir = find_run_dir(repeat)
        log_path = find_log(run_dir)
        run_work = work / f"r{repeat}"
        batch_dir = run_work / "batches"
        replay_dir = run_work / "replay"
        statements = read_statements(log_path)
        rows = write_batches(statements, batch_dir, args.batch_size, args.max_batches)
        status_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok = docker_replay_repeat(
            args=args,
            repeat=repeat,
            batch_dir=batch_dir,
            replay_dir=replay_dir,
            checkpoints=checkpoints,
        )
        status_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for cp in checkpoints:
            cov_json = replay_dir / f"t{cp}.cov.json"
            report_txt = replay_dir / f"t{cp}.report.txt"
            complete = ok and cov_json.exists()
            append_index_row(
                index,
                {
                    "run_id": f"sqlite_sqlancer_r{repeat}",
                    "tool": args.tool,
                    "dbms": "sqlite",
                    "repeat_id": repeat,
                    "checkpoint_min": cp,
                    "cov_json": cov_json if complete else "",
                    "report_txt": report_txt if complete else "",
                    "status": "complete" if complete else "failed",
                    "message": note if complete else f"docker replay failed; inspect {replay_dir}",
                    "container_image": args.image,
                    "binary": args.binary,
                    "seed_count": len(rows),
                    "seed_corpus": log_path,
                    "build_id": args.image,
                    "container_id": run_dir.name,
                    "version": "SQLite llvmcov SQLancer statement-order replay",
                    "start_time": status_start,
                    "end_time": status_end,
                },
            )

    summary_cmd = [
        "python3",
        str(REPLAY_DIR / "summarize_llvm_cov.py"),
        "--target-regions",
        str(target_regions),
        "--replay-index",
        str(index),
        "--out",
        str(real),
        "--tool",
        args.tool,
        "--sqlite-amalgamation",
        str(sqlite_map),
    ]
    run(summary_cmd)

    if args.apply_result:
        backup = BASE / "result" / f"backup_before_sqlancer_sqlite_{ts}"
        existing_sources = read_result_sources()
        if (BASE / "result").exists():
            shutil.copytree(BASE / "result", backup)
        run(
            [
                "python3",
                str(BASE / "scripts" / "apply_real_squirrel_pg_result.py"),
                "--merge-into-existing",
                "--tool",
                args.tool,
                "--real-data",
                str(real),
            ]
        )
        refresh_extra_figures()
        update_readme(out, real, repeats, note, existing_sources)
        print(f"backup={backup}")

    print(out)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
