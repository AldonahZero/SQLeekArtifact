#!/usr/bin/env python3
"""Parse CodeQL CSV outputs into Stage 1 targets for one DBMS."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("/root/SQLeek")))
from config import BUG_TYPES, DBMS_LIST, FALLBACK_TARGETS  # noqa: E402

STAGE_DIR = Path("/root/SQLeek/sqleek_pipeline/stage1_static")
OUTPUT_DIR = STAGE_DIR / "output"
TARGETS_DIR = OUTPUT_DIR / "targets"
CODEQL_RESULTS = OUTPUT_DIR / "codeql_results"
ROOT = Path("/root/SQLeek")

FALLBACK_CHAINS: dict[str, list[dict[str, object]]] = {
    "mysql": [
        {
            "entry": "mysql_execute_command",
            "danger_fn": "copy_inner",
            "depth": 4,
            "path": ["mysql_execute_command", "execute_sqlcom_select", "JOIN::exec", "copy_inner"],
            "source": "fallback",
        },
        {
            "entry": "dispatch_command",
            "danger_fn": "alloc_root",
            "depth": 3,
            "path": ["dispatch_command", "mysql_parse", "alloc_root"],
            "source": "fallback",
        },
    ],
    "mariadb": [
        {
            "entry": "mysql_execute_command",
            "danger_fn": "copy_inner",
            "depth": 4,
            "path": ["mysql_execute_command", "execute_sqlcom_select", "JOIN::exec", "copy_inner"],
            "source": "fallback",
        }
    ],
    "sqlite": [
        {
            "entry": "sqlite3_exec",
            "danger_fn": "sqlite3VdbeExec",
            "depth": 3,
            "path": ["sqlite3_exec", "sqlite3_prepare_v2", "sqlite3_step", "sqlite3VdbeExec"],
            "source": "fallback",
        }
    ],
    "monetdb": [
        {
            "entry": "monetdbe_query_internal",
            "danger_fn": "SQLengine_",
            "depth": 2,
            "path": ["monetdbe_query_internal", "SQLengine_"],
            "source": "fallback",
        },
        {
            "entry": "SQLengine_",
            "danger_fn": "SQLparser",
            "depth": 2,
            "path": ["SQLengine_", "SQLparser"],
            "source": "fallback",
        },
        {
            "entry": "SQLparser",
            "danger_fn": "runMALsequence",
            "depth": 4,
            "path": ["SQLparser", "SQLparser_body", "SQLengine_", "runMALsequence"],
            "source": "fallback",
        },
    ],
}


def log(message: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUTPUT_DIR / "build.log").open("a", encoding="utf-8") as fp:
        fp.write(f"[parse_targets] {message}\n")
    print(f"[parse_targets] {message}")


def die(message: str) -> None:
    log(message)
    sys.exit(1)


def dbms_csv_dir(dbms: str) -> Path:
    return CODEQL_RESULTS / dbms


def require_nonempty_csv(dbms: str, path: Path, label: str) -> None:
    if not path.is_file():
        die(f"{dbms}: missing {label}: {path}")
    if path.stat().st_size == 0:
        die(f"{dbms}: empty {label}: {path}")


def safe_rows(path: Path) -> list[list[str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows: list[list[str]] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        for row in csv.reader(fp):
            if row:
                rows.append(row)
    return rows


def infer_location(row: list[str]) -> str | None:
    joined = " ".join(row)
    match = re.search(r"([A-Za-z0-9_./+-]+\.(?:c|cc|cpp|h|hpp))[:\"]?,?\s*(\d+)", joined)
    if match:
        return f"{Path(match.group(1)).name}:{match.group(2)}"
    return None


def parse_stale_descriptor(csv_path: Path) -> list[str]:
    targets: list[str] = []
    for row in safe_rows(csv_path):
        loc = infer_location(row)
        if loc:
            targets.append(loc)
    return sorted(set(targets))


def parse_memory_priority_csv(csv_path: Path) -> list[str]:
    targets: list[str] = []
    for row in safe_rows(csv_path):
        loc = infer_location(row)
        if loc:
            targets.append(loc)
    return sorted(set(targets))


def parse_dbms_callchain_csv(csv_path: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in safe_rows(csv_path):
        # Preferred format (dbms_callchain.ql @kind table):
        #   entry,danger_fn,depth
        if len(row) >= 3 and row[0] and row[1]:
            try:
                depth = int(row[2])
                entry = row[0].strip()
                danger = row[1].strip()
            except ValueError:
                depth = -1
            if depth > 0:
                out.append(
                    {
                        "entry": entry,
                        "danger_fn": danger,
                        "depth": depth,
                        "path": [entry, danger],
                    }
                )
                continue

        # Legacy fallback: parse from a free-form message column.
        # CodeQL CSV may coalesce multiple alerts into a single row by joining messages with newlines.
        text = " ".join(row)
        for line in [ln.strip() for ln in text.splitlines() if ln.strip()]:
            m = re.search(r"depth=(\d+)\s+(\S+)\s+.*?(?:→\*|->\*)\s*(\S+)", line)
            if m:
                depth, entry, danger = int(m.group(1)), m.group(2), m.group(3)
            else:
                m2 = re.search(r"depth=(\d+)", line, re.I)
                if not m2:
                    continue
                depth = int(m2.group(1))
                toks = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", line)
                entry = toks[0] if toks else "exec_simple_query"
                danger = toks[-1] if len(toks) > 1 else ""
            if not danger:
                continue
            out.append(
                {
                    "entry": entry,
                    "danger_fn": danger,
                    "depth": max(1, depth),
                    "path": [entry, danger],
                }
            )
    return out


def chains_for_scheduler(chains: list[dict[str, object]]) -> list[dict[str, object]]:
    sched: list[dict[str, object]] = []
    for c in chains:
        path = c.get("path")
        if not isinstance(path, list) or not path:
            path = [c.get("entry"), c.get("danger_fn")]
        sched.append(
            {
                "entry": c.get("entry"),
                "target": c.get("danger_fn"),
                "depth": c.get("depth"),
                "functions": [str(x) for x in path if x],
            }
        )
    return sched


def parse_memory_targets(dbms: str) -> list[str]:
    targets: list[str] = []
    for row in safe_rows(CODEQL_RESULTS / dbms / "memory_sinks.csv"):
        loc = infer_location(row)
        if loc and loc not in targets:
            targets.append(loc)
    if not targets:
        log(f"{dbms}: no memory targets produced by CodeQL")
    return targets


def parse_logic_targets(dbms: str) -> list[str]:
    targets: list[str] = []
    for row in safe_rows(CODEQL_RESULTS / dbms / "logic_patterns.csv"):
        loc = infer_location(row)
        if loc and loc not in targets:
            targets.append(loc)
    return targets


def parse_depth(row_text: str) -> int:
    match = re.search(r"depth\s+(\d+)", row_text, re.IGNORECASE)
    if match:
        return max(1, int(match.group(1)))
    return 10


def parse_callchains_legacy() -> dict[str, list[dict[str, object]]]:
    chains: dict[str, list[dict[str, object]]] = {dbms: [] for dbms in DBMS_LIST}
    for dbms in DBMS_LIST:
        for row in safe_rows(CODEQL_RESULTS / dbms / "callchain_extractor.csv"):
            text = " ".join(row)
            functions = re.findall(r"\b[A-Za-z_][A-Za-z0-9_:]*\b", text)
            functions = [
                f for f in functions if f not in {"Call", "chain", "depth", "from", "to", "the", "and"}
            ]
            if len(functions) < 2:
                continue
            depth = parse_depth(text)
            entry = functions[0]
            danger = functions[-1]
            chains[dbms].append(
                {
                    "entry": entry,
                    "target": danger,
                    "depth": depth,
                    "functions": functions[: min(len(functions), depth + 1)],
                    "source": "codeql",
                }
            )
    return chains


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote {path}")


def fallback_memory_targets(dbms: str) -> list[str]:
    targets = FALLBACK_TARGETS.get(f"{dbms}_memory", [])
    if targets:
        log(f"{dbms}: using fallback memory targets ({len(targets)})")
    return list(targets)


def fallback_callchains(dbms: str) -> list[dict[str, object]]:
    chains = FALLBACK_CHAINS.get(dbms, [])
    if chains:
        log(f"{dbms}: using fallback callchains ({len(chains)})")
    return [dict(chain) for chain in chains]


def run_dbms_specialized(dbms: str) -> tuple[list[str], list[str], list[dict[str, object]]]:
    cdir = dbms_csv_dir(dbms)
    stale_csv = cdir / "dbms_stale_descriptor.csv"
    mem_csv = cdir / "dbms_memory_sinks.csv"
    chain_csv = cdir / "dbms_callchain.csv"

    if dbms == "postgres":
        require_nonempty_csv(dbms, stale_csv, "dbms_stale_descriptor.csv")
        require_nonempty_csv(dbms, mem_csv, "dbms_memory_sinks.csv")
        require_nonempty_csv(dbms, chain_csv, "dbms_callchain.csv")

    stale = parse_stale_descriptor(stale_csv)
    if dbms == "postgres" and not stale:
        die("postgres: dbms_stale_descriptor.csv produced no file:line locations")

    memory = parse_memory_priority_csv(mem_csv)
    if not memory:
        if dbms == "postgres":
            die("postgres: dbms_memory_sinks.csv produced no file:line locations")
        memory = fallback_memory_targets(dbms)

    chains = parse_dbms_callchain_csv(chain_csv)
    if not chains:
        if dbms == "postgres":
            die("postgres: dbms_callchain.csv produced no call chains")
        chains = fallback_callchains(dbms)

    merged_mem = sorted(set(memory + stale))
    if not merged_mem:
        die(f"{dbms}: merged memory targets empty")
    if not chains:
        die(f"{dbms}: no call chains produced by CodeQL or fallback")

    return stale, merged_mem, chains


def verification(
    all_memory: list[str],
    stale: list[str],
    chains: list[dict[str, object]],
) -> list[str]:
    log("=== Stage 1 verification (strict) ===")
    mem_set = set(all_memory)
    # Line numbers are version-dependent; enforce file-level anchors instead.
    required_files = {"varlena.c", "rowtypes.c", "execExprInterp.c"}
    present_files = {s.split(":", 1)[0] for s in mem_set if ":" in s}
    missing_files = sorted(required_files - present_files)
    if missing_files:
        die(f"verification: required files missing from merged memory targets: {missing_files}")
    chain_fns = {str(c.get("danger_fn")) for c in chains}
    for fn in ("text_to_cstring", "record_out", "ExecEvalRow"):
        if fn not in chain_fns:
            die(f"verification: required danger_fn {fn} missing from call chains")
    if not any("execExprInterp" in s or "execExpr" in s for s in stale):
        die("verification: postgres_stale must include at least one execExpr* / interp location")
    log("verification OK")
    return sorted(mem_set)


def entry_map_from_scheduler_rows(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    by_entry: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        entry = str(row.get("entry") or "")
        target = str(row.get("target") or row.get("danger_fn") or "")
        depth = int(row.get("depth") or 1)
        path = row.get("functions") or row.get("path") or [entry, target]
        if not entry or not target:
            continue
        by_entry[entry].append(
            {
                "danger_fn": target,
                "depth": max(1, depth),
                "path": [str(x) for x in path if x],
            }
        )
    return dict(by_entry)


def load_existing_callchains() -> dict[str, object]:
    path = TARGETS_DIR / "callchains.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_callchains_payload(dbms: str, chains: list[dict[str, object]]) -> None:
    scheduler_rows = chains_for_scheduler(chains)
    by_entry = entry_map_from_scheduler_rows(scheduler_rows)

    payload = load_existing_callchains()
    for existing_dbms in DBMS_LIST:
        existing_rows = payload.get(existing_dbms)
        if isinstance(existing_rows, list):
            payload[existing_dbms] = existing_rows

    payload.update(
        {
            "source": "dbms_specialized_queries",
            "active_dbms": dbms,
            f"{dbms}_csv_dir": str(dbms_csv_dir(dbms)),
            "chains": chains,
            "by_entry": by_entry,
            dbms: scheduler_rows,
        }
    )
    (TARGETS_DIR / "callchains.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"wrote {TARGETS_DIR / 'callchains.json'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dbms", default=os.environ.get("SQLEEK_DBMS", "postgres"), choices=DBMS_LIST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dbms = args.dbms
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)

    stale, merged_mem, chains = run_dbms_specialized(dbms)
    write_lines(TARGETS_DIR / f"{dbms}_stale.txt", sorted(set(stale)))

    if dbms == "postgres":
        merged_mem = verification(merged_mem, stale, chains)
    write_lines(TARGETS_DIR / f"{dbms}_memory.txt", merged_mem)

    logic_targets = parse_logic_targets(dbms)
    if logic_targets:
        write_lines(TARGETS_DIR / f"{dbms}_logic.txt", logic_targets)

    write_callchains_payload(dbms, chains)

    weights = {dbms: {bug_type: 1.0 for bug_type in BUG_TYPES} for dbms in DBMS_LIST}
    (TARGETS_DIR / "weights.json").write_text(
        json.dumps(weights, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"wrote {TARGETS_DIR / 'weights.json'}")

    root_log = ROOT / "build.log"
    try:
        with root_log.open("a", encoding="utf-8") as fh:
            fh.write(f"[parse_targets] {dbms}_memory={len(merged_mem)} stale={len(stale)} chains={len(chains)}\n")
    except OSError:
        pass

    log(f"Stage 1 parse_targets complete for {dbms}")


if __name__ == "__main__":
    main()
