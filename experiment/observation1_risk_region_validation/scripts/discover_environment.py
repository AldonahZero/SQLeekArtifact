#!/usr/bin/env python3
"""Read-only environment inventory for Observation 1."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

from common import canonical_dbms, default_config, dump_yaml, ensure_tree, exp_dir, git, load_config, report_dir, run


def sh(cmd: str) -> str:
    proc = run(["bash", "-lc", cmd], check=False)
    text = proc.stdout
    if proc.stderr.strip():
        text += ("\n" if text else "") + proc.stderr
    return text.strip()


def repo_info(path: Path) -> dict[str, str]:
    if not (path / ".git").exists():
        return {"path": str(path), "exists": str(path.exists()), "is_git_repo": "false"}
    out = {"path": str(path), "exists": "true", "is_git_repo": "true"}
    for key, args in {
        "commit": ["rev-parse", "HEAD"],
        "branch": ["branch", "--show-current"],
        "first_commit": ["log", "--date=short", "--format=%H %ad %s", "--reverse"],
        "latest_commit": ["log", "--date=short", "--format=%H %ad %s", "-n", "1"],
        "status_short": ["status", "--short"],
    }.items():
        try:
            val = git(path, *args, check=False)
            if key == "first_commit":
                val = val.splitlines()[0] if val.splitlines() else ""
            out[key] = val.strip()
        except Exception as exc:
            out[key] = f"error: {exc}"
    return out


def high_cpu_affinity(ps_text: str) -> list[dict[str, str]]:
    rows = []
    for line in ps_text.splitlines()[1:]:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, pcpu, psr, comm, args = parts
        try:
            if float(pcpu) < 1.0:
                continue
        except ValueError:
            continue
        affinity = sh(f"taskset -pc {pid} 2>/dev/null || true")
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "pcpu": pcpu,
                "last_cpu": psr,
                "command": comm,
                "args": args,
                "affinity": affinity,
            }
        )
    return rows


def find_running_experiments(ps_text: str, docker_text: str, tmux_text: str) -> list[str]:
    hits = []
    patterns = re.compile(r"(?i)(sqleek|fuzz|afl|griffin|replay|coverage|rq2|stage3|llvm|profdata)")
    for source, text in [("ps", ps_text), ("docker", docker_text), ("tmux", tmux_text)]:
        for line in text.splitlines():
            if patterns.search(line):
                hits.append(f"[{source}] {line}")
    return hits


def ensure_configs(root: Path) -> dict[str, dict[str, str]]:
    configs = {}
    for dbms in ["mysql", "postgresql"]:
        cfg = load_config(dbms)
        defaults = default_config(dbms)
        for key, value in defaults.items():
            cfg.setdefault(key, value)
        cfg["sqleek_root"] = str(root)
        if dbms == "mysql":
            cfg.setdefault("source_repo", str(root / "sources" / "mysql"))
            cfg.setdefault("source_path", str(root / "sources" / "mysql"))
        else:
            cfg.setdefault("source_repo", str(root / "sources" / "postgres"))
            cfg.setdefault("source_path", str(root / "sources" / "postgres"))
        dump_yaml(exp_dir() / "configs" / f"{dbms}.yaml", cfg)
        configs[dbms] = cfg
    return configs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("SQLEEK_ROOT", "/root/SQLeek"))
    args = parser.parse_args()

    ensure_tree()
    root = Path(args.root).resolve()
    configs = ensure_configs(root)

    tmux_text = sh("tmux list-sessions 2>/dev/null || true")
    docker_text = sh("docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true")
    ps_text = sh("ps -eo pid,ppid,pcpu,psr,comm,args --sort=-pcpu | head -n 40")
    affinity = high_cpu_affinity(ps_text)
    experiments = find_running_experiments(ps_text, docker_text, tmux_text)

    repos = {
        "sqleek": repo_info(root),
        "mysql": repo_info(Path(configs["mysql"]["source_repo"])),
        "postgresql": repo_info(Path(configs["postgresql"]["source_repo"])),
        "tmp_pg_src": repo_info(Path("/tmp/pg_src")),
    }

    inventory = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": sh("hostname"),
        "root": str(root),
        "tmux_sessions": tmux_text,
        "docker_containers": docker_text,
        "top_cpu_processes": ps_text,
        "high_cpu_affinity": affinity,
        "running_sqleek_experiments": experiments,
        "repositories": repos,
    }
    (exp_dir() / "data" / "environment_inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    def fenced(text: str) -> str:
        return "```text\n" + (text.strip() or "(none)") + "\n```"

    repo_lines = []
    for name, info in repos.items():
        repo_lines.append(f"### {name}\n")
        for key, val in info.items():
            repo_lines.append(f"- {key}: `{val}`")
        repo_lines.append("")

    affinity_lines = [
        "| PID | CPU% | Last CPU | Command | Affinity |",
        "|---:|---:|---:|---|---|",
    ]
    for row in affinity:
        affinity_lines.append(
            f"| {row['pid']} | {row['pcpu']} | {row['last_cpu']} | `{row['command']}` | `{row['affinity']}` |"
        )
    if len(affinity_lines) == 2:
        affinity_lines.append("|  |  |  | none above threshold |  |")

    report = [
        "# Environment Inventory",
        "",
        f"- Generated at UTC: `{inventory['generated_at_utc']}`",
        f"- Host: `{inventory['host']}`",
        f"- Experiment directory: `{exp_dir()}`",
        "",
        "## Safety Notes",
        "",
        "- This inventory is read-only.",
        "- No tmux session, Docker container, image, source tree, cache, fuzzing output, replay output, or coverage output was stopped, restarted, deleted, or modified.",
        "- Python/Git analysis scripts should be run with low priority and an explicit CPU set if heavy processing is needed.",
        "",
        "## tmux Sessions",
        "",
        fenced(tmux_text),
        "",
        "## Docker Containers",
        "",
        fenced(docker_text),
        "",
        "## High CPU Processes",
        "",
        fenced(ps_text),
        "",
        "## CPU Affinity",
        "",
        "\n".join(affinity_lines),
        "",
        "## Running SQLeek/Fuzzing/Replay/Coverage/RQ2 Processes",
        "",
        fenced("\n".join(experiments)),
        "",
        "## Source Repositories",
        "",
        "\n".join(repo_lines),
    ]
    (report_dir() / "environment_inventory.md").write_text("\n".join(report), encoding="utf-8")
    print(report_dir() / "environment_inventory.md")


if __name__ == "__main__":
    main()
