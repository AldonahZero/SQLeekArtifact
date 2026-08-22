#!/usr/bin/env python3
from __future__ import annotations
import csv
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/root/SQLeek/experiment/RQ2")
SOURCE = ROOT / "collected/sqlaser_formal_24h_20260821_ssh_docker_replay_inputs_20260822/mariadb"
OUTPUT = ROOT / "replay/output"
IMAGE = "griffin_mariadb_llvmcov:latest"
RUNNER = ROOT / "replay/container_replay_llvm_bucketed_mariadb.sh"
SUPPORT = ROOT / "replay/support/mariadb_llvmcov"
TARGET_REGIONS = ROOT / "result/audit/squirrel_mariadb/target_regions.csv"
CHECKPOINTS = "3600000,10800000,18000000,28800000,36000000,43200000,54000000,72000000,86400000"
PROFILE = "/tmp/rq2_prof/%p-%m.profraw"

stamp = os.environ.get("SQLASER_MARIADB_REPLAY_STAMP") or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
run_id = "sqlaser_mariadb_llvm_replay_" + stamp
run_root = OUTPUT / run_id
run_root.mkdir(parents=True, exist_ok=False)
(run_root / "target_regions.csv").write_bytes(TARGET_REGIONS.read_bytes())
(run_root / "run_root.path").write_text(str(run_root) + "\n")
(OUTPUT / "sqlaser_mariadb_latest.path").write_text(str(run_root) + "\n")
with (run_root / "run_meta.tsv").open("w", encoding="utf-8") as fp:
    fp.write("metric\tvalue\n")
    for k, v in [
        ("source_stage", SOURCE),
        ("tool", "SQLaser"),
        ("dbms", "MariaDB"),
        ("image", IMAGE),
        ("checkpoints_min", "60,180,300,480,600,720,900,1200,1440"),
        ("llvm_cov_export_options", "--skip-functions --skip-expansions"),
        ("profile_pattern", PROFILE),
        ("started_utc", dt.datetime.now(dt.timezone.utc).isoformat()),
    ]:
        fp.write(f"{k}\t{v}\n")

status_path = run_root / "replay_status.tsv"
fields = ["run", "container", "container_id", "status", "exit_code", "started_utc", "ended_utc", "output_dir"]
status_rows: dict[str, dict[str, str]] = {}
with status_path.open("w", newline="", encoding="utf-8") as fp:
    writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for repeat in [f"r{i}" for i in range(1, 6)]:
        queue = SOURCE / repeat / "queue"
        out = run_root / repeat
        out.mkdir()
        queue_count = sum(1 for p in queue.iterdir() if p.is_file())
        if queue_count == 0:
            raise RuntimeError(f"empty queue: {queue}")
        name = f"{run_id}_{repeat}"
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        command = [
            "docker", "run", "-d", "--rm", "--name", name,
            "--cpus=8", "--memory=24g", "--memory-swap=24g", "--shm-size=4g",
            "--env", f"LLVM_PROFILE_FILE={PROFILE}",
            "--env", "RQ2_REPLAY_FLUSH_SERVER_AT_CHECKPOINT=1",
            "--env", "LLVM_PROFDATA_BIN=llvm-profdata-12",
            "--env", "LLVM_COV_BIN=llvm-cov-12",
            "-v", f"{queue}:/rq2_queue:ro",
            "-v", f"{out}:/rq2_out",
            "-v", f"{RUNNER}:/rq2_runner.sh:ro",
            "-v", f"{SUPPORT}:/rq2_support:ro",
            IMAGE, "/bin/bash", "-lc",
            "cp /rq2_support/env.sh /workspace/scripts/env.sh && "
            "cp /rq2_support/reset_lv1.sh /workspace/scripts/reset_lv1.sh && "
            "cp /rq2_support/testt_replay.sh /workspace/scripts/testt_replay.sh && "
            "cp /rq2_support/odbc.ini /workspace/configs/odbc.ini && "
            "exec /rq2_runner.sh "
            "--dbms mysql "
            "--binary /root/bin_llvmcov/usr/local/mysql/bin/mariadbd "
            f"--checkpoints-ms {CHECKPOINTS} "
            "--seed-timeout 120 "
            "--out-prefix /rq2_out/replay "
            "--reset-script /workspace/scripts/reset_lv1.sh "
            "--test-script /workspace/scripts/testt_replay.sh "
            "--process-name my_8888",
        ]
        cid = subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
        row = {
            "run": repeat, "container": name, "container_id": cid,
            "status": "running", "exit_code": "", "started_utc": started,
            "ended_utc": "", "output_dir": str(out),
        }
        status_rows[repeat] = row
        writer.writerow(row)
        print(f"started {repeat} queue_files={queue_count} container={name} id={cid}", flush=True)

def rewrite_status() -> None:
    with status_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for repeat in [f"r{i}" for i in range(1, 6)]:
            writer.writerow(status_rows[repeat])

failed = False
for repeat in [f"r{i}" for i in range(1, 6)]:
    row = status_rows[repeat]
    result = subprocess.run(["docker", "wait", row["container_id"]], capture_output=True, text=True)
    rc = result.stdout.strip() if result.returncode == 0 else "125"
    row["status"] = "exited"
    row["exit_code"] = rc
    row["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    failed = failed or rc != "0"
    rewrite_status()
    print(f"finished {repeat} rc={rc}", flush=True)

with (run_root / "run_meta.tsv").open("a", encoding="utf-8") as fp:
    fp.write(f"ended_utc\t{dt.datetime.now(dt.timezone.utc).isoformat()}\n")
    fp.write(f"status\t{'failed' if failed else 'all_5_replay_containers_exit0'}\n")
sys.exit(1 if failed else 0)
