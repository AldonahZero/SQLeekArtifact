#!/usr/bin/env python3
"""Launch all repeats of an RQ4 w/o-M1 campaign concurrently."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import traceback
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def log(path: Path, message: str) -> None:
    line = f"[{now_iso()}] {message}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    print(line, flush=True)


def inspect_container(name: str) -> dict[str, str]:
    fmt = "{{.State.Status}}\t{{.State.ExitCode}}\t{{.State.OOMKilled}}\t{{.State.StartedAt}}\t{{.State.FinishedAt}}"
    result = subprocess.run(
        ["docker", "inspect", name, "--format", fmt],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"inspect_error": result.stderr.strip() or result.stdout.strip()}
    values = result.stdout.strip().split("\t")
    values.extend([""] * (5 - len(values)))
    return {
        "docker_state": values[0],
        "docker_exit_code": values[1],
        "oom_killed": values[2],
        "started_at": values[3],
        "finished_at": values[4],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    status_path = Path(plan["status_path"])
    runtime_log = Path(plan["runtime_log_path"])
    runs_root = Path(plan["runs_root"])
    cfg = plan["config"]
    run_dbms = str(cfg.get("dbms_arg") or cfg.get("dbms") or "mariadb")
    if run_dbms == "postgres":
        run_dbms = "postgresql"
    run_id_prefix = str(plan.get("run_id_prefix") or f"rq4_wo_m1_{run_dbms}")
    repeat_count = int(plan["repeat_count"])
    seed_dir = Path(cfg["seed_dir"])
    target_dir = Path(cfg["target_dir"])
    if not seed_dir.is_dir() or not any(seed_dir.glob("*")):
        raise SystemExit(f"seed directory is empty: {seed_dir}")
    if not target_dir.is_dir():
        raise SystemExit(f"target directory is missing: {target_dir}")

    status: dict[str, Any] = {
        "campaign_id": plan["campaign_id"],
        "variant": plan["variant"],
        "created_at_utc": plan["created_at_utc"],
        "launcher_started_at_utc": now_iso(),
        "state": "launching",
        "execution": f"parallel_{repeat_count}_{run_dbms}_containers",
        "repeat_count": repeat_count,
        "runs": [],
    }
    write_json(status_path, status)
    env = {str(k): str(v) for k, v in cfg["env"].items()}
    merged_env = {**os.environ, **env}
    launchers: list[tuple[int, str, Path, subprocess.Popen[str]]] = []

    try:
        for repeat in range(1, repeat_count + 1):
            timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
            run_id = f"{run_id_prefix}_r{repeat}_{timestamp}"
            command = [
                plan["run_script"],
                run_dbms,
                run_id,
                plan["duration"],
                str(seed_dir),
                str(target_dir),
                str(runs_root),
            ]
            launch_log = runtime_log.parent / f"launch_{run_id}.log"
            launch_log.parent.mkdir(parents=True, exist_ok=True)
            stream = launch_log.open("w", encoding="utf-8")
            log(runtime_log, f"launch parallel repeat={repeat} run_id={run_id}")
            process = subprocess.Popen(
                command,
                env=merged_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
            launchers.append((repeat, run_id, launch_log, process))

        run_infos: list[dict[str, Any]] = []
        for repeat, run_id, launch_log, process in launchers:
            returncode = process.wait()
            if returncode != 0:
                raise RuntimeError(
                    f"run.sh failed for repeat={repeat} run_id={run_id} rc={returncode}; log={launch_log}"
                )
            run_root = runs_root / run_dbms / run_id
            container_path = run_root / "meta" / "container.txt"
            container = container_path.read_text(encoding="utf-8").strip()
            run_info: dict[str, Any] = {
                "repeat": repeat,
                "run_id": run_id,
                "container": container,
                "run_root": str(run_root),
                "seed_dir": str(seed_dir),
                "target_dir": str(target_dir),
                "launch_log": str(launch_log),
                "launched_at_utc": now_iso(),
                "status": "running",
            }
            run_infos.append(run_info)
        status["runs"] = sorted(run_infos, key=lambda item: int(item["repeat"]))
        status["state"] = "running"
        write_json(status_path, status)
        log(runtime_log, f"all {repeat_count} containers launched; waiting concurrently")

        waiters: list[tuple[dict[str, Any], subprocess.Popen[str]]] = []
        for run_info in run_infos:
            waiter = subprocess.Popen(
                ["docker", "wait", str(run_info["container"])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            waiters.append((run_info, waiter))
        for run_info, waiter in waiters:
            stdout, stderr = waiter.communicate()
            run_info.update(
                {
                    "docker_wait_returncode": waiter.returncode,
                    "docker_wait_stdout": stdout.strip(),
                    "docker_wait_stderr": stderr.strip(),
                    "completed_at_utc": now_iso(),
                    "status": "completed",
                }
            )
            run_info.update(inspect_container(str(run_info["container"])))
            write_json(status_path, status)
            log(
                runtime_log,
                f"complete repeat={run_info['repeat']} container={run_info['container']} "
                f"exit={run_info.get('docker_exit_code')}",
            )

        status["state"] = "completed"
        status["completed_at_utc"] = now_iso()
        write_json(status_path, status)
        log(runtime_log, "parallel campaign complete")
    except Exception:
        status["state"] = "failed"
        status["failed_at_utc"] = now_iso()
        status["error"] = traceback.format_exc()
        write_json(status_path, status)
        log(runtime_log, "parallel campaign failed")
        raise


if __name__ == "__main__":
    main()
