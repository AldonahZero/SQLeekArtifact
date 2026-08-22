#!/usr/bin/env python3
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path("/root/SQLeek")))
from config import DBMS_LIST

STAGE_DIR = Path("/root/SQLeek/sqleek_pipeline/stage4_triage")
REPORT_DIR = STAGE_DIR / "output"
SEEDS_DIR = Path("/root/SQLeek/sqleek_pipeline/stage2_setup/output/seeds")


def log(message: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / "build.log").open("a", encoding="utf-8") as fp:
        fp.write(f"[logic_oracle] {message}\n")
    print(f"[logic_oracle] {message}")


def run_sqlite(sql: str) -> tuple[int, str]:
    try:
        con = sqlite3.connect(":memory:")
        con.executescript(sql)
        rows = con.execute("SELECT name, type FROM sqlite_master ORDER BY 1, 2").fetchall()
        con.close()
        return 0, repr(rows)
    except Exception as exc:
        return 1, str(exc)


def run_cli(cmd: str, sql: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            input=sql.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            timeout=20,
        )
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    except Exception as exc:
        return 127, str(exc)


def oracle_pair(dbms: str, sql: str) -> list[tuple[str, int, str]]:
    results = []
    if dbms == "sqlite":
        results.append(("python_sqlite3", *run_sqlite(sql)))
        if shutil.which("sqlite3"):
            with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fp:
                fp.write(sql)
                tmp = fp.name
            try:
                results.append(("sqlite3_cli", *run_cli(f"sqlite3 < {tmp}", "")))
            finally:
                Path(tmp).unlink(missing_ok=True)
    elif dbms == "postgres" and shutil.which("psql"):
        results.append(("psql", *run_cli("psql -X -q -v ON_ERROR_STOP=1", sql)))
    elif dbms == "mysql" and shutil.which("mysql"):
        results.append(("mysql", *run_cli("mysql --batch --raw", sql)))
    return results


def normalize_output(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    findings = []

    for dbms in DBMS_LIST:
        seed_dir = SEEDS_DIR / dbms / "logic"
        if not seed_dir.exists():
            continue
        for seed in sorted(seed_dir.glob("*.sql")):
            sql = seed.read_text(encoding="utf-8", errors="replace")
            results = oracle_pair(dbms, sql)
            if len(results) < 2:
                continue
            normalized = {name: normalize_output(output) for name, _code, output in results}
            signatures = {hashlib.sha256(value.encode()).hexdigest() for value in normalized.values()}
            if len(signatures) > 1:
                findings.append({
                    "id": hashlib.sha256(str(seed).encode()).hexdigest()[:16],
                    "dbms": dbms,
                    "bug_type": "logic",
                    "seed": str(seed),
                    "message": "LOGIC_BUG: differential oracle mismatch",
                    "results": [
                        {"engine": name, "returncode": code, "output": output[:2000]}
                        for name, code, output in results
                    ],
                })

    out = REPORT_DIR / "logic_report.json"
    out.write_text(json.dumps({"logic_bugs": findings}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log(f"wrote {out}")


if __name__ == "__main__":
    main()
