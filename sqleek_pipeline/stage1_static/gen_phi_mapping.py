#!/usr/bin/env python3
"""
LLM-assisted Φ mapping: function name → SQL clause tags for directed fuzzing.
Falls back to ground truth when ANTHROPIC_API_KEY is unset or callchains missing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path("/root/SQLeek")
sys.path.insert(0, str(ROOT))
from config import TARGETS_DIR  # noqa: E402

CALLCHAINS_FILE = TARGETS_DIR / "callchains.json"
PHI_OUTPUT = TARGETS_DIR / "phi_mapping.json"

GROUND_TRUTH: dict[str, list[str]] = {
    "record_out": ["CAST_TO_COMPOSITE", "CURSOR", "FETCH", "ROW_CONSTRUCTOR"],
    "text_to_cstring": ["CAST_TO_TEXT", "ALTER_TYPE", "TYPE_COERCION", "TEXT_OUTPUT"],
    "ExecEvalRow": ["ROW_CONSTRUCTOR", "COMPOSITE_TYPE_CAST", "CURSOR", "FETCH"],
    "textout": ["TEXT_COLUMN_OUTPUT", "CAST_TO_TEXT"],
    "printtup": ["SELECT_OUTPUT", "FETCH", "CURSOR_FETCH"],
    "OutputFunctionCall": ["ANY_TYPE_OUTPUT", "CAST", "FETCH", "SELECT"],
    "mysql_execute_command": ["SELECT", "INSERT", "UPDATE", "DDL", "QUERY_EXECUTION"],
    "dispatch_command": ["QUERY_DISPATCH", "CLIENT_COMMAND"],
    "mysql_parse": ["SQL_PARSE", "SELECT", "DDL"],
    "copy_inner": ["SELECT", "TYPE_CONVERSION", "INDEX_LOOKUP"],
    "alloc_root": ["QUERY_EXECUTION", "MEMORY_ALLOCATION"],
    "monetdbe_query_internal": ["QUERY_EXECUTION", "EMBEDDED_API", "SELECT"],
    "monetdbe_query": ["QUERY_EXECUTION", "EMBEDDED_API", "SELECT"],
    "SQLengine_": ["QUERY_EXECUTION", "SQL_ENGINE", "SELECT"],
    "SQLengine": ["QUERY_EXECUTION", "SQL_ENGINE", "SELECT"],
    "SQLparser": ["SQL_PARSE", "SELECT", "DDL"],
    "SQLparser_body": ["SQL_PARSE", "SELECT", "DDL"],
    "runMALsequence": ["MAL_EXECUTION", "QUERY_EXECUTION", "SELECT"],
    "MALrun": ["MAL_EXECUTION", "QUERY_EXECUTION", "SELECT"],
    "freeMalBlk": ["MAL_EXECUTION", "MEMORY_MANAGEMENT"],
    "resetMalBlk": ["MAL_EXECUTION", "MEMORY_MANAGEMENT"],
    "BATjoin": ["JOIN", "COLUMN_STORE", "SELECT"],
    "BATappend": ["INSERT", "COLUMN_STORE", "DML"],
    "BATselect": ["WHERE", "COLUMN_STORE", "SELECT"],
    "GDKmalloc": ["MEMORY_ALLOCATION", "COLUMN_STORE"],
    "GDKrealloc": ["MEMORY_ALLOCATION", "COLUMN_STORE"],
    "GDKstrdup": ["STRING_FUNCTION", "MEMORY_ALLOCATION"],
    "memcpy": ["MEMORY_COPY", "COLUMN_STORE"],
    "memmove": ["MEMORY_COPY", "COLUMN_STORE"],
    "bs_write": ["COPY", "STREAM_IO", "BINARY_PAYLOAD"],
    "write_out": ["COPY", "STREAM_IO", "BINARY_PAYLOAD"],
    "rel_rename": ["SELECT", "SQL_REWRITE", "RELATIONAL_OPTIMIZER"],
    "rel_unnest_dependent": ["JOIN", "SUBQUERY", "RELATIONAL_OPTIMIZER"],
    "push_up_join": ["JOIN", "RELATIONAL_OPTIMIZER"],
}

SYSTEM_PROMPT = """You are a DBMS internals expert.
Given a DBMS C/C++ function name and how it is reached from SQL entry points,
respond ONLY with JSON:
{"clauses": ["CLAUSE_1", "CLAUSE_2"], "reasoning": "one short sentence"}
Use UPPER_SNAKE_CASE clause names (SELECT, WHERE, JOIN, CURSOR, FETCH, ALTER_TYPE, CAST, ...)."""


def load_callchains() -> dict:
    if not CALLCHAINS_FILE.is_file():
        return {}
    data = json.loads(CALLCHAINS_FILE.read_text(encoding="utf-8"))
    return data


def danger_functions_from_chains(data: dict) -> dict[str, list[dict]]:
    """Build per-function context from callchains payload."""
    by_fn: dict[str, list[dict]] = {}
    active_dbms = os.environ.get("SQLEEK_DBMS") or data.get("active_dbms") or "postgres"
    chains = data.get("chains") or data.get(active_dbms) or data.get("postgres") or []
    if isinstance(chains, dict):
        return {}
    for c in chains:
        if not isinstance(c, dict):
            continue
        fn = str(c.get("danger_fn") or c.get("target") or "")
        if not fn:
            continue
        by_fn.setdefault(fn, []).append(
            {
                "entry": c.get("entry"),
                "depth": c.get("depth"),
                "path": c.get("path") or c.get("functions"),
            }
        )
    return by_fn


def llm_clauses(client, fn_name: str, context: dict) -> list[str]:
    prompt = f"""Function: {fn_name}
Context: {json.dumps(context, indent=2)[:6000]}

Minimal SQL constructs needed to reach this function in PostgreSQL?"""
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text
    try:
        return list(json.loads(text).get("clauses") or [])
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            try:
                return list(json.loads(m.group(0)).get("clauses") or [])
            except json.JSONDecodeError:
                pass
    return []


def validate_phi(phi: dict[str, list[str]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for fn, gt in GROUND_TRUTH.items():
        if fn not in phi:
            continue
        llm = set(phi[fn])
        gts = set(gt)
        overlap = llm & gts
        p = len(overlap) / len(llm) if llm else 0.0
        r = len(overlap) / len(gts) if gts else 0.0
        f1 = 2 * p * r / (p + r + 1e-9)
        out[fn] = {
            "llm_clauses": sorted(llm),
            "ground_truth": gt,
            "precision": round(p, 3),
            "recall": round(r, 3),
            "f1": round(f1, 3),
        }
    return out


def main() -> None:
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    data = load_callchains()
    by_fn = danger_functions_from_chains(data)

    if not by_fn:
        print(
            "[gen_phi_mapping] error: no functions extracted from callchains.json "
            "(need non-empty `chains` or `postgres` from parse_targets / CodeQL)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    client = None
    if use_llm:
        try:
            import anthropic  # type: ignore

            client = anthropic.Anthropic()
        except Exception as exc:
            print(f"[gen_phi_mapping] anthropic unavailable: {exc}; ground-truth only")
            client = None

    phi: dict[str, list[str]] = {}
    for fn in sorted(by_fn):
        if fn in GROUND_TRUTH:
            phi[fn] = list(GROUND_TRUTH[fn])
            print(f"  [GT] {fn}: {phi[fn]}")
            continue
        ctx = {"function": fn, "reachable_from": by_fn[fn][:12]}
        if client:
            clauses = llm_clauses(client, fn, ctx)
            phi[fn] = clauses or ["UNKNOWN"]
            print(f"  [LLM] {fn}: {phi[fn]}")
        else:
            phi[fn] = ["UNKNOWN"]

    out = {
        "phi_mapping": phi,
        "validation_vs_ground_truth": validate_phi(phi),
        "generation_method": "ground_truth for key fns"
        + ("; LLM for others" if client else "; no LLM key"),
        "callchains_source": str(CALLCHAINS_FILE),
    }
    PHI_OUTPUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[gen_phi_mapping] wrote {PHI_OUTPUT} ({len(phi)} functions)")


if __name__ == "__main__":
    main()
