#!/usr/bin/env python3
"""Generate queries/priority_functions.qll from Stage 0 priority scores."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path("/root/SQLeek")
STAGE0_OUT = ROOT / "sqleek_pipeline" / "stage0_pre_processing" / "output"
DEFAULT_OUT = ROOT / "sqleek_pipeline" / "stage1_static" / "queries" / "priority_functions.qll"

FALLBACK_PRIORITY_FUNCTIONS = {
    "mysql": [
        "mysql_execute_command",
        "dispatch_command",
        "do_command",
        "mysql_parse",
        "my_malloc",
        "my_realloc",
        "alloc_root",
        "sql_alloc",
        "memdup_root",
        "strmake_root",
        "save_in_field",
        "copy_inner",
        "type_conversion_status_to_store_key",
        "test_quick_select",
        "get_mm_tree",
        "filesort",
    ],
    "mariadb": [
        "mysql_execute_command",
        "dispatch_command",
        "do_command",
        "mysql_parse",
        "my_malloc",
        "my_realloc",
        "alloc_root",
        "sql_alloc",
        "memdup_root",
        "strmake_root",
        "save_in_field",
        "copy_inner",
        "test_quick_select",
        "get_mm_tree",
        "filesort",
    ],
    "sqlite": [
        "sqlite3_exec",
        "sqlite3_prepare_v2",
        "sqlite3_step",
        "sqlite3VdbeExec",
        "sqlite3DbMallocRaw",
        "sqlite3DbMallocZero",
        "sqlite3Malloc",
        "sqlite3GetVarint",
        "sqlite3Read",
    ],
    "monetdb": [
        "SQLparser",
        "SQLengine",
        "SQLprepare",
        "SQLexecutePrepared",
        "mvc_init",
        "mvc_destroy",
        "rel_optimizer",
        "rel_selects",
        "rel_push_select",
        "rel_unnest_dependent",
        "sql_bind",
        "runMALsequence",
        "mal_interpreter",
        "MALrun",
        "OPTtopImplementation",
        "BATnew",
        "BATappend",
        "BATselect",
        "BATjoin",
        "GDKmalloc",
        "GDKrealloc",
        "GDKstrdup",
        "atomRead",
        "stream_read",
        "stream_write",
        "SQLExecDirect",
        "SQLExecute",
        "SQLFetch",
        "monetdbe_query",
    ],
}


def resolve_stage0(dbms: str, path: Path | None) -> Path | None:
    if path and path.is_file():
        return path
    candidates = [
        STAGE0_OUT / f"{dbms}_priority_scores.json",
        ROOT / "output" / f"{dbms}_priority_scores.json",
    ]
    if dbms == "postgres":
        candidates.extend(
            [
                STAGE0_OUT / "priority_scores.json",
                ROOT / "output" / "priority_scores.json",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def family_balanced(names: list[str], functions: dict, top: int) -> list[str]:
    pools: dict[str, list[str]] = {}
    for name, row in functions.items():
        if not isinstance(row, dict):
            continue
        for family in row.get("risk_families", []) or []:
            pools.setdefault(str(family), []).append(str(name))

    if not pools:
        return names[:top]

    reserve_per_family = 2 if top >= len(pools) * 2 else 1
    reserved: list[str] = []
    for family in sorted(pools):
        ranked = sorted(
            pools[family],
            key=lambda name: float(functions.get(name, {}).get("priority", 0)),
            reverse=True,
        )
        reserved.extend(ranked[:reserve_per_family])

    seen: set[str] = set()
    out: list[str] = []
    for name in [*reserved, *names]:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= top:
            break
    return out


def load_selected(stage0: Path, top: int, dbms: str) -> list[str]:
    data = json.loads(stage0.read_text(encoding="utf-8"))
    selected = data.get("selected_functions") or []
    if not selected:
        raise SystemExit(f"[gen_priority_qll] {stage0} has no selected_functions")
    if isinstance(selected[0], dict):
        names = [str(x.get("function", x)) for x in selected]
    else:
        names = [str(x) for x in selected]
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    if dbms == "monetdb":
        return family_balanced(out, data.get("functions") or {}, top)
    return out[:top]


def fallback_selected(dbms: str, top: int) -> list[str]:
    return FALLBACK_PRIORITY_FUNCTIONS.get(dbms, [])[:top]


def ql_escape(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"unsafe function name for QL literal: {name!r}")
    return name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbms", default="postgres", choices=["sqlite", "postgres", "mysql", "mariadb", "monetdb"])
    ap.add_argument("--stage0", type=Path, default=None)
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    stage0 = resolve_stage0(args.dbms, args.stage0)
    if stage0:
        names = load_selected(stage0, args.top, args.dbms)
        source = str(stage0)
    elif args.dbms == "postgres":
        raise SystemExit(f"[gen_priority_qll] missing {STAGE0_OUT / 'priority_scores.json'}; pass --stage0")
    else:
        names = fallback_selected(args.dbms, args.top)
        source = f"builtin fallback for {args.dbms}"

    if not names:
        names = ["__sqleek_no_priority_function__"]

    lines = [
        "/** Auto-generated by tools/gen_priority_qll.py; do not edit. */",
        f"// DBMS: {args.dbms}; source: {source}",
        "import cpp",
        "",
    ]
    lines.append("predicate isPriorityFunction(Function f) {")
    parts = [f'  f.getName() = "{ql_escape(n)}"' for n in names]
    lines.append("\n  or\n".join(parts))
    lines.append("}")
    lines.append("")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gen_priority_qll] dbms={args.dbms} source={source} wrote {len(names)} names -> {args.output}")


if __name__ == "__main__":
    main()
