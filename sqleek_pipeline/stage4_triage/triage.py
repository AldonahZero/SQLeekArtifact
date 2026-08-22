#!/usr/bin/env python3
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/root/SQLeek")))
from config import DBMS_LIST, RUNTIME_CONFIG

STAGE_DIR = Path("/root/SQLeek/sqleek_pipeline/stage4_triage")
STAGE3_OUTPUT_DIR = Path("/root/SQLeek/sqleek_pipeline/stage3_scheduler/output")
OUTPUT_DIR = STAGE3_OUTPUT_DIR / "fuzz"
REPORT_DIR = STAGE_DIR / "output"


MEMORY_MARKERS = (
    "heap-buffer-overflow",
    "stack-buffer-overflow",
    "global-buffer-overflow",
    "use-after-free",
    "double-free",
    "SEGV",
    "AddressSanitizer",
    "UndefinedBehaviorSanitizer",
)


def log(message: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / "build.log").open("a", encoding="utf-8") as fp:
        fp.write(f"[triage] {message}\n")
    print(f"[triage] {message}")


def load_runtime() -> dict:
    if not RUNTIME_CONFIG.exists():
        return {}
    return json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))


def crash_files() -> list[tuple[str, str, Path]]:
    crashes = []
    for dbms in DBMS_LIST:
        for bug_type in ("memory", "logic"):
            cdir = OUTPUT_DIR / f"{dbms}_{bug_type}" / "default" / "crashes"
            if not cdir.exists():
                continue
            for path in sorted(cdir.iterdir()):
                if path.is_file() and not path.name.startswith("README"):
                    crashes.append((dbms, bug_type, path))
    return crashes


def stack_signature(text: str, fallback_bytes: bytes) -> str:
    frames = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "force_exit_all" in stripped:
            frames.append(stripped)
        if len(frames) >= 6:
            break
    if not frames:
        return hashlib.sha256(fallback_bytes).hexdigest()[:16]
    return hashlib.sha256("\n".join(frames).encode()).hexdigest()[:16]


def classify(stderr: str, path: Path) -> str:
    if "LOGIC_BUG:" in stderr or path.name.lower().startswith("logic"):
        return "logic"
    if any(marker in stderr for marker in MEMORY_MARKERS):
        return "memory"
    return "unknown"


def rerun_in_container(dbms: str, crash: Path, runtime: dict) -> tuple[int, str]:
    entry = runtime.get(dbms, {})
    container = entry.get("container")
    harness = entry.get("harness") or "/workspace/scripts/testt"
    if not container:
        return 127, "No Griffin container configured"

    try:
        data = crash.read_bytes()
        cmd = [
            "docker", "exec", "-i",
            "-e", "ASAN_OPTIONS=abort_on_error=1:detect_leaks=0:symbolize=1:handle_sigfpe=1",
            container,
            "bash", "-lc",
            f"export LD_LIBRARY_PATH=/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies:/workspace/bld_griffin/custom_mutator/squirrel_dependencies:$LD_LIBRARY_PATH; '{harness}'",
        ]
        proc = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    except Exception as exc:
        return 127, f"rerun failed: {exc}"


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    runtime = load_runtime()
    unique: dict[str, dict] = {}
    all_crashes = crash_files()

    for dbms, expected_type, path in all_crashes:
        data = path.read_bytes()
        returncode, stderr = rerun_in_container(dbms, path, runtime)
        signature = stack_signature(stderr, data)
        bug_type = classify(stderr, path)
        if bug_type == "unknown":
            bug_type = expected_type

        unique.setdefault(signature, {
            "id": signature,
            "dbms": dbms,
            "bug_type": bug_type,
            "crash_file": str(path),
            "returncode": returncode,
            "stack_hash": signature,
            "stack_excerpt": "\n".join(stderr.splitlines()[:40]),
            "duplicates": 0,
        })
        unique[signature]["duplicates"] += 1

    report = {
        "total_crashes": len(all_crashes),
        "unique_crashes": len(unique),
        "bugs": sorted(unique.values(), key=lambda item: (item["dbms"], item["bug_type"], item["id"])),
    }
    out = REPORT_DIR / "crash_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log(f"wrote {out}")


if __name__ == "__main__":
    main()
