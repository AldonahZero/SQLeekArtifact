#!/usr/bin/env python3
"""
PGQS: Patch-Guided QL Synthesis Algorithm (Stage 1 core).

This implementation is intentionally pragmatic:
  - ψ extracted by Extract(Δ): output/patch_features.json
  - Duality validation if DB_before available: q(DB_before) ⊃ q(DB_after)
  - Score(q) = w1·Precision + w2·Recall + w3·Dual
  - Bounded convergence: up to K_MAX iterations

If LLM is disabled (SQLEEK_LLM_ENABLED != 1 in config.env / env):
  - fallback mode: keep the existing queries/pgqs.ql as q0
  - log mode = fallback_no_llm

For non-PostgreSQL DBMSes, PGQS defaults to keeping the checked-in manual
<dbms>_pgqs.ql. Set PGQS_FORCE_LLM=1 to explicitly re-enable LLM synthesis.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

try:
    from .rule_equivalence import (
        canonicalize_rule,
        deduplicate_validated_rules,
        rule_signature,
    )
except ImportError:  # Direct execution: python3 tools/pgqs.py
    from rule_equivalence import (
        canonicalize_rule,
        deduplicate_validated_rules,
        rule_signature,
    )

ROOT = Path("/root/SQLeek")
STAGE1 = ROOT / "sqleek_pipeline" / "stage1_static"
OUT_DIR = STAGE1 / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_DBMS = os.environ.get("SQLEEK_DBMS", "postgres").lower()
QL_OUT = STAGE1 / "queries" / "pgqs.ql"
SYNTH_LOG = OUT_DIR / "pgqs_synthesis_log.json"
RULE_IR_OUT = OUT_DIR / "pgqs_rule_ir.json"

PATCH_FEATURES = OUT_DIR / "patch_features.json"
DB_PATHS = OUT_DIR / "db_paths.json"

# Hyperparameters (can be overridden via env)
W1 = float(os.environ.get("PGQS_W1", "0.3"))
W2 = float(os.environ.get("PGQS_W2", "0.3"))
W3 = float(os.environ.get("PGQS_W3", "0.4"))
K_MAX = int(os.environ.get("PGQS_K_MAX", "5"))
SCORE_THRESHOLD = float(os.environ.get("PGQS_SCORE_THRESHOLD", "0.6"))
LLM_TIMEOUT_SECONDS = int(os.environ.get("PGQS_LLM_TIMEOUT_SECONDS", "70"))


def configure_dbms(dbms: str) -> None:
    global CURRENT_DBMS, QL_OUT, SYNTH_LOG, RULE_IR_OUT

    CURRENT_DBMS = dbms
    os.environ["SQLEEK_DBMS"] = dbms
    if dbms == "postgres":
        QL_OUT = STAGE1 / "queries" / "pgqs.ql"
        SYNTH_LOG = OUT_DIR / "pgqs_synthesis_log.json"
        RULE_IR_OUT = OUT_DIR / "pgqs_rule_ir.json"
    else:
        QL_OUT = STAGE1 / "queries" / f"{dbms}_pgqs.ql"
        SYNTH_LOG = OUT_DIR / f"{dbms}_pgqs_synthesis_log.json"
        RULE_IR_OUT = OUT_DIR / f"{dbms}_pgqs_rule_ir.json"


def load_priority_functions() -> set[str]:
    """
    Load 𝒻_T from Stage 0.

    Prefer the DBMS-prefixed Stage 0 output under sqleek_pipeline/; keep the old
    PostgreSQL priority_scores.json path as a compatibility fallback.
    """
    dbms = os.environ.get("SQLEEK_DBMS", "postgres").lower()
    candidates = [
        ROOT / "sqleek_pipeline" / "stage0_pre_processing" / "output" / f"{dbms}_priority_scores.json",
        ROOT / "output" / f"{dbms}_priority_scores.json",
    ]
    if dbms == "postgres":
        candidates.extend([
            ROOT / "sqleek_pipeline" / "stage0_pre_processing" / "output" / "priority_scores.json",
            ROOT / "output" / "priority_scores.json",
        ])
    for p in candidates:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            sel = data.get("selected_functions") or []
            names: list[str] = []
            for x in sel:
                if isinstance(x, dict):
                    names.append(str(x.get("function", x)))
                else:
                    names.append(str(x))
            return set(names)
    raise SystemExit(f"[pgqs] missing Stage 0 priority scores for dbms={dbms}")


def compile_ql(ql_path: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["codeql", "query", "compile", str(ql_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        ok = result.returncode == 0
        msg = (result.stderr or result.stdout or "")[:1500]
        return ok, msg
    except Exception as exc:
        return False, str(exc)


def strip_markdown_fences(text: str) -> str:
    """
    Some models return markdown fences despite instructions.
    Strip a single surrounding fenced code block if present.
    """
    ql_text = text.strip()
    if not ql_text.startswith("```"):
        return ql_text

    lines = ql_text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def ensure_codeql_metadata(ql_text: str, dbms: str) -> str:
    """
    CodeQL accepts compiling queries without metadata, but
    `codeql database analyze --format=csv` fails later without @kind.
    Make generated PGQS queries analyzable even when the LLM omits metadata.
    """
    if re.search(r"(?m)^\s*\*\s*@kind\s+", ql_text):
        return ql_text

    safe_dbms = re.sub(r"[^a-z0-9_-]+", "-", dbms.lower()).strip("-") or "dbms"
    title_dbms = safe_dbms.replace("-", " ").replace("_", " ").title()
    metadata = f"""/**
 * @name {title_dbms} PGQS stale descriptor candidate
 * @description Finds DBMS metadata or cached descriptor accesses in priority functions synthesized by PGQS.
 * @kind problem
 * @problem.severity warning
 * @id dbms/{safe_dbms}/pgqs-stale-descriptor
 */
"""
    return metadata + ql_text.lstrip()


def normalize_generated_ql(text: str, dbms: str) -> str:
    return ensure_codeql_metadata(strip_markdown_fences(text), dbms)


def parse_llm_rule_response(text: str) -> tuple[str, list[list[object]] | None, str]:
    """Unpack the structured rule envelope without parsing CodeQL text.

    Pure QL output remains accepted as a compatibility path, but it has no IR
    and therefore cannot participate in predicate-form deduplication.
    """
    response_text = strip_markdown_fences(text)
    if not response_text.startswith("{"):
        return response_text, None, "legacy_query"

    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid structured rule JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("structured rule response must be a JSON object")

    query = payload.get("query")
    predicates = payload.get("predicates")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("structured rule response must contain a non-empty query")
    if not isinstance(predicates, list):
        raise ValueError("structured rule response must contain a predicates list")

    # Validate the explicit variable tags and the exact-canonicalization bound
    # now, before the executable query is written.
    canonicalize_rule({"predicates": predicates})
    return query, predicates, "structured_ir"


def write_rule_ir(
    candidates: list[dict],
    deduplicated_rules: list[dict] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "dbms": CURRENT_DBMS,
        "equivalence_basis": "llm_declared_predicate_ir",
        "variable_encoding": {"var": "<local-variable-name>"},
        "candidates": candidates,
        "deduplicated_rules": deduplicated_rules or [],
    }
    RULE_IR_OUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_rule_ir_candidates() -> list[dict]:
    """Load prior candidates so equivalent rules can accumulate across fixes."""
    if not RULE_IR_OUT.exists():
        return []
    try:
        payload = json.loads(RULE_IR_OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load existing rule IR {RULE_IR_OUT}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"existing rule IR {RULE_IR_OUT} must be a JSON object")
    if str(payload.get("dbms") or "").lower() != CURRENT_DBMS:
        raise ValueError(
            f"existing rule IR {RULE_IR_OUT} belongs to dbms={payload.get('dbms')!r}"
        )

    candidates = payload.get("candidates")
    if candidates is None:
        # Compatibility with an early compact output that persisted only one
        # representative per group. Expand its provenance so replacing one fix
        # cannot discard the other supporting fixes.
        representatives = payload.get("deduplicated_rules", [])
        if not isinstance(representatives, list):
            raise ValueError(
                f"existing rule IR {RULE_IR_OUT} has invalid deduplicated_rules"
            )
        candidates = []
        for representative in representatives:
            if not isinstance(representative, dict):
                raise ValueError(
                    f"existing rule IR {RULE_IR_OUT} has an invalid representative"
                )
            supporting_fixes = representative.get("supporting_fixes")
            valid_supporting_fixes = (
                [fix_id for fix_id in supporting_fixes if fix_id not in (None, "")]
                if isinstance(supporting_fixes, list)
                else []
            )
            if valid_supporting_fixes:
                for supporting_fix in valid_supporting_fixes:
                    candidate = dict(representative)
                    candidate["fix_id"] = str(supporting_fix)
                    candidate.pop("supporting_fixes", None)
                    candidate.pop("support_count", None)
                    candidates.append(candidate)
            else:
                candidates.append(representative)
    if not isinstance(candidates, list) or not all(
        isinstance(candidate, dict) for candidate in candidates
    ):
        raise ValueError(f"existing rule IR {RULE_IR_OUT} has invalid candidates")
    for index, candidate in enumerate(candidates):
        if str(candidate.get("dbms") or "").lower() != CURRENT_DBMS:
            raise ValueError(
                f"existing rule IR candidate {index} belongs to "
                f"dbms={candidate.get('dbms')!r}"
            )
    return candidates


def should_keep_manual_dbms_query(dbms: str) -> bool:
    if dbms == "postgres":
        return False
    force_llm = os.environ.get("PGQS_FORCE_LLM", "0").lower() in {"1", "true", "yes"}
    return not force_llm


def run_ql_query(ql_path: Path, db_path: str) -> tuple[bool, list[list[str]], str]:
    """
    Run a QL query against a CodeQL DB. Returns (ok, rows, err).
    """
    if not Path(db_path).exists():
        return False, [], f"DB not found: {db_path}"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fp:
        out_csv = fp.name
    try:
        result = subprocess.run(
            [
                "codeql",
                "database",
                "analyze",
                db_path,
                str(ql_path),
                "--format=csv",
                f"--output={out_csv}",
                "--rerun",
                "--search-path=/root/codeql/qlpacks",
            ],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode != 0:
            return False, [], (result.stderr or result.stdout or "")[:1500]
        import csv

        rows: list[list[str]] = []
        with open(out_csv, newline="", encoding="utf-8", errors="replace") as cf:
            for row in csv.reader(cf):
                if row:
                    rows.append(row)
        return True, rows, ""
    except subprocess.TimeoutExpired:
        return False, [], "TIMEOUT"
    except Exception as exc:
        return False, [], str(exc)


def compute_score(rows_before: list[list[str]], rows_after: list[list[str]], priority_fns: set[str]) -> dict:
    """
    Score(q) = w1·Precision + w2·Recall + w3·Dual

    Practical approximation:
      - Extract identifier-like tokens from CSV cell strings.
      - Consider a "hit" if any priority function name appears in tokens.
    """
    fn_pat = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")

    text_before = " ".join(" ".join(r) for r in rows_before)
    text_after = " ".join(" ".join(r) for r in rows_after)
    fns_before = set(fn_pat.findall(text_before))
    fns_after = set(fn_pat.findall(text_after))

    hit_before = fns_before & priority_fns
    hit_after = fns_after & priority_fns

    n_before = max(len(fns_before), 1)
    result_set_before = {tuple(row) for row in rows_before}
    result_set_after = {tuple(row) for row in rows_after}
    after_is_subset = result_set_after.issubset(result_set_before)

    precision = len(hit_before) / n_before
    recall = len(hit_before) / max(len(priority_fns), 1)
    dual = (
        len(result_set_before - result_set_after) / max(len(result_set_before), 1)
        if after_is_subset
        else 0.0
    )
    dual_holds = result_set_before > result_set_after
    score = W1 * precision + W2 * recall + W3 * dual

    return {
        "score": round(score, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "dual": round(dual, 4),
        "dual_holds": dual_holds,
        "result_count_before": len(result_set_before),
        "result_count_after": len(result_set_after),
        "hit_targets_before": sorted(hit_before),
        "hit_targets_after": sorted(hit_after),
        "false_positives": sorted(hit_after),
    }


def top_priority_for_prompt(priority_fns: set[str], dbms: str) -> list[str]:
    if dbms == "postgres":
        preferred = [f for f in priority_fns if any(k in f for k in ("Exec", "record", "text_to", "print", "Output"))]
    elif dbms in {"mysql", "mariadb"}:
        preferred = [
            f
            for f in priority_fns
            if any(k.lower() in f.lower() for k in ("item", "field", "join", "opt", "exec", "copy", "alloc", "parse"))
        ]
    else:
        preferred = list(priority_fns)
    return sorted(preferred or list(priority_fns), key=len)[:15]


def target_pattern_for_dbms(dbms: str) -> str:
    if dbms in {"mysql", "mariadb"}:
        return """- Find risky local uses around MySQL parser/optimizer/executor priority functions.
- Prefer memory-copy/allocation size arguments, Item/Field/Table metadata access, and cached field/table descriptor usage.
- Use Stage 0 𝒻_T as the main anchor; report sites whose enclosing function is in or near high-priority functions.
- Avoid inventing call chains; this Query A should only match local code patterns."""
    if dbms == "sqlite":
        return """- Find risky local uses around SQLite parser/VDBE priority functions.
- Prefer varint/record decode, allocation size, and memcpy/memmove-style sink patterns.
- Use Stage 0 𝒻_T as the main anchor; do not invent call chains."""
    if dbms == "monetdb":
        return """- Find risky local uses around MonetDB SQL compiler, relational rewrite, MAL runtime, GDK/BAT storage, stream, and embedded API priority functions.
- Prefer SQL rel/exp/mvc state access, MAL block/stack state, BAT/GDK helper calls, stream buffer operations, and local allocation/copy sinks.
- Use Stage 0 𝒻_T as the main anchor; report local code patterns only and do not invent call chains."""
    return """- Find cached tuple/type descriptor access patterns that are unsafe when row types change.
- Use added identifiers/guards from ψ as anchors.
- Avoid obvious false positives: fixed code should be excluded when possible."""


def load_dbms_notes(dbms: str) -> str:
    notes_path = ROOT / "sqleek_pipeline" / "llm" / "skills" / "dbms_profiles" / f"{dbms}.md"
    return notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""


def build_generation_prompt(
    psi: dict,
    priority_fns: set[str],
    prev_ql: str,
    feedback: dict | None,
    iteration: int,
    dbms: str,
) -> str:
    top_priority = top_priority_for_prompt(priority_fns, dbms)
    dbms_notes = load_dbms_notes(dbms)

    base = f"""You are a CodeQL expert synthesizing a security query for {dbms} C/C++ code.

## Formal Input ψ = Extract(Δ fix patch)
A⁺ (identifiers ADDED by fix): {psi.get('A_plus', [])}
A⁻ (identifiers REMOVED by fix): {psi.get('A_minus', [])}
C⁺ (control flow added): {[c[0] for c in psi.get('C_plus', [])]}
G (fix invariant guard): {psi.get('G', 'unknown')}
Affected files: {psi.get('affected_files', [])}

## Constraint: Priority Functions 𝒻_T
Prefer reporting sites whose enclosing function is in 𝒻_T (high priority). Example subset:
{top_priority}

## DBMS-specific notes
{dbms_notes[:4000] if dbms_notes else '(none)'}

## Target pattern
{target_pattern_for_dbms(dbms)}

## CodeQL requirements
- Must compile with CodeQL C/C++ packs (import cpp).
- Start with a CodeQL metadata block containing @name, @description, @kind problem, @problem.severity, and @id.
- Output: (element, message, …) compatible with codeql database analyze --format=csv.
- Keep under 120 lines.

## Structured rule output
Return one JSON object:
{{
  "predicates": [
    ["insidePriorityFunction", {{"var": "access"}}, {{"var": "function"}}],
    ["readsCachedDescriptor", {{"var": "access"}}, "tupDesc"],
    ["not", "guardedByFreshnessCheck", {{"var": "access"}}]
  ],
  "query": "the complete executable .ql file as a JSON string"
}}

`predicates` is a flat AND-connected semantic conjunction. Every atom starts
with its predicate name. Mark only local rule variables as {{"var": "name"}};
leave function/API/field names and constants as ordinary JSON values. Do not
include commit hashes, bug IDs, or file/line anchors. Use at most 8 distinct
local variables.

Output ONLY the JSON object. No markdown fences. No explanation.
"""

    if feedback and iteration > 1:
        base += f"""
## Feedback from iteration {iteration-1}
score={feedback.get('score')} precision={feedback.get('precision')} recall={feedback.get('recall')} dual={feedback.get('dual')}
dual_holds={feedback.get('dual_holds')}
false_positives(priority hits after fix)={feedback.get('false_positives', [])[:20]}
compile_error={str(feedback.get('compile_error', ''))[:500]}

Previous query:
{prev_ql}
"""
    return base


def llm_generate_query(prompt: str, *, dbms: str = "postgres") -> tuple[bool, str]:
    """
    Generate a structured rule envelope using the shared LLM client.
    """
    # Defensive: some OpenAI-compatible endpoints can hang despite SDK timeouts.
    # Run the request in a subprocess and hard-timeout it.
    try:
        import multiprocessing as mp
        import sys

        def _worker(p: str, dbms_name: str, q: "mp.Queue[str]") -> None:  # type: ignore[name-defined]
            try:
                sys.path.insert(0, str(ROOT))
                from sqleek_pipeline.llm import OpenAILLMClient  # type: ignore

                llm = OpenAILLMClient()
                # Render DBMS-specific fields in the skill so the protocol is reusable across DBMSes.
                skill = llm.load_skill("pgqs_query_a")
                notes_path = ROOT / "sqleek_pipeline" / "llm" / "skills" / "dbms_profiles" / f"{dbms_name}.md"
                dbms_notes = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
                system_prompt = (
                    skill.replace("{{dbms}}", dbms_name).replace("{{dbms_notes}}", dbms_notes)
                )
                text = llm.complete(system_prompt, p, temperature=0.0)
                q.put(text or "")
            except Exception as exc:  # pragma: no cover
                q.put(f"llm_error: {exc}")

        q: "mp.Queue[str]" = mp.Queue()  # type: ignore[name-defined]
        proc = mp.Process(target=_worker, args=(prompt, dbms, q))
        proc.start()
        proc.join(timeout=LLM_TIMEOUT_SECONDS)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            return False, "llm_error: timeout"
        text = q.get_nowait() if not q.empty() else ""
        if text.startswith("llm_error:"):
            return False, text
        return True, text
    except Exception as exc:
        return False, f"llm_error: {exc}"


def pgqs() -> dict:
    print(f"[pgqs] start dbms={CURRENT_DBMS} output={QL_OUT}", flush=True)
    if CURRENT_DBMS == "postgres" and not PATCH_FEATURES.exists():
        raise SystemExit("[pgqs] missing output/patch_features.json; run extract_patch_features.py first")
    if CURRENT_DBMS == "postgres":
        psi = json.loads(PATCH_FEATURES.read_text(encoding="utf-8"))
    else:
        psi = {
            "A_plus": [],
            "A_minus": [],
            "C_plus": [],
            "C_minus": [],
            "G": f"stage0_priority_functions_for_{CURRENT_DBMS}",
            "affected_files": [],
            "patch_stats": {},
            "note": "non-postgres PGQS currently uses Stage 0 priority functions without patch delta",
        }
    fix_id = str(psi.get("fix_id") or "").strip()
    priority_fns = load_priority_functions()

    db_paths = {}
    if DB_PATHS.exists():
        db_paths = json.loads(DB_PATHS.read_text(encoding="utf-8"))
    db_before = str(db_paths.get("db_before", ""))
    db_after = str(db_paths.get("db_after", ""))
    dual_available = bool(db_paths.get("dual_available")) and db_before and db_after

    # Important: config.env is not automatically sourced into the process environment.
    # Use the shared LLM config loader so Stage 1 behaves consistently with llm/client.py.
    llm_enabled = False
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from sqleek_pipeline.llm.client import load_llm_config  # type: ignore

        llm_enabled = bool(load_llm_config().enabled)
    except Exception:
        # Fallback to env var if loader isn't available for some reason.
        llm_enabled = os.environ.get("SQLEEK_LLM_ENABLED", "0").lower() in {"1", "true", "yes"}
    keep_manual_query = should_keep_manual_dbms_query(CURRENT_DBMS)
    mode = "manual_non_postgres" if keep_manual_query else ("llm" if llm_enabled else "fallback_no_llm")
    print(f"[pgqs] mode={mode} dual_available={dual_available}", flush=True)
    prior_candidates = load_rule_ir_candidates()
    rule_candidates: list[dict] = prior_candidates
    validated_rules: list[dict] = [
        candidate
        for candidate in prior_candidates
        if candidate.get("validated") is True
    ]
    deduplicated_rules = deduplicate_validated_rules(validated_rules)
    synthesis_log: dict = {
        "algorithm": "PGQS",
        "k_max": K_MAX,
        "weights": {"w1": W1, "w2": W2, "w3": W3},
        "dual_available": dual_available,
        "dbms": CURRENT_DBMS,
        "ql_output": str(QL_OUT),
        "rule_ir_output": str(RULE_IR_OUT),
        "mode": mode,
        "iterations": [],
        "validated_rules": deduplicated_rules,
        "convergence": False,
        "final_score": 0.0,
    }
    write_rule_ir(rule_candidates, deduplicated_rules)

    # fallback/manual: keep existing QL file as q0
    if synthesis_log["mode"] in {"fallback_no_llm", "manual_non_postgres"}:
        if not QL_OUT.exists():
            raise SystemExit(
                f"[pgqs] {synthesis_log['mode']} requires existing {QL_OUT}"
            )
        print(f"[pgqs] keep existing {QL_OUT}", flush=True)
        fallback_ql = normalize_generated_ql(QL_OUT.read_text(encoding="utf-8"), CURRENT_DBMS)
        QL_OUT.write_text(fallback_ql + "\n", encoding="utf-8")
        ok, err = compile_ql(QL_OUT)
        synthesis_log["iterations"].append(
            {
                "iteration": 0,
                "compiled": ok,
                "compile_error": "" if ok else err,
                "note": f"{synthesis_log['mode']}; kept existing {QL_OUT.name}",
            }
        )
        SYNTH_LOG.write_text(json.dumps(synthesis_log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return synthesis_log

    best_ql = ""
    best_score = -1.0
    prev_ql = ""
    feedback = None
    current_rule_candidates: list[dict] = []
    replaced_current_fix = False

    for i in range(1, K_MAX + 1):
        prompt = build_generation_prompt(psi, priority_fns, prev_ql, feedback, i, CURRENT_DBMS)
        print(f"[pgqs] iter={i}: calling llm", flush=True)
        ok_llm, ql_raw = llm_generate_query(prompt, dbms=CURRENT_DBMS)

        iter_log: dict = {"iteration": i, "llm_ok": ok_llm}
        if not ok_llm:
            iter_log["error"] = ql_raw
            iter_log["compiled"] = False
            iter_log["score"] = 0.0
            synthesis_log["iterations"].append(iter_log)
            break

        try:
            ql_raw, predicates, rule_ir_mode = parse_llm_rule_response(ql_raw)
        except ValueError as exc:
            error = str(exc)
            iter_log.update(
                {
                    "rule_ir_mode": "invalid_structured_ir",
                    "rule_ir_error": error,
                    "compiled": False,
                    "score": 0.0,
                }
            )
            synthesis_log["iterations"].append(iter_log)
            feedback = {
                "score": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "dual": 0.0,
                "dual_holds": False,
                "compile_error": error,
            }
            continue

        iter_log["rule_ir_mode"] = rule_ir_mode
        ql_text = normalize_generated_ql(ql_raw, CURRENT_DBMS)
        candidate_rule: dict | None = None
        if predicates is not None:
            candidate_rule = {
                "dbms": CURRENT_DBMS,
                "iteration": i,
                "ir_source": "llm_declared",
                "predicates": predicates,
                "query": ql_text,
                "validated": False,
                "validation": {"status": "pending"},
            }
            if fix_id:
                candidate_rule["fix_id"] = fix_id
            candidate_rule["rule_signature"] = rule_signature(candidate_rule)
            iter_log["rule_signature"] = candidate_rule["rule_signature"]
            current_rule_candidates.append(candidate_rule)
            rule_candidates.append(candidate_rule)
            # Persist the IR before the executable query is written.  Query text
            # is carried only as the eventual representative and is never parsed
            # or used when computing equivalence.
            write_rule_ir(rule_candidates, deduplicated_rules)

        print(f"[pgqs] iter={i}: llm_ok; writing {QL_OUT.name} ({len(ql_text)} chars)", flush=True)
        QL_OUT.write_text(ql_text + "\n", encoding="utf-8")

        print(f"[pgqs] iter={i}: compiling", flush=True)
        compiled, compile_err = compile_ql(QL_OUT)
        iter_log["compiled"] = compiled
        iter_log["compile_error"] = "" if compiled else compile_err[:500]
        if not compiled:
            if candidate_rule is not None:
                candidate_rule["validation"] = {
                    "status": "rejected",
                    "compiled": False,
                    "compile_error": iter_log["compile_error"],
                }
                write_rule_ir(rule_candidates, deduplicated_rules)
            synthesis_log["iterations"].append(iter_log)
            feedback = {
                "score": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "dual": 0.0,
                "dual_holds": False,
                "compile_error": iter_log.get("compile_error", ""),
            }
            prev_ql = ql_text
            continue
        print(f"[pgqs] iter={i}: compiled_ok", flush=True)

        if dual_available:
            ok_b, rows_b, err_b = run_ql_query(QL_OUT, db_before)
            ok_a, rows_a, err_a = run_ql_query(QL_OUT, db_after)
            iter_log["db_before_ok"] = ok_b
            iter_log["db_after_ok"] = ok_a
            if ok_b and ok_a:
                scores = compute_score(rows_b, rows_a, priority_fns)
                iter_log.update(scores)
                feedback = scores
            else:
                iter_log["note"] = f"db_run_failed: {err_b or err_a}"
                iter_log.update({"score": 0.3, "precision": 0.0, "recall": 0.0, "dual": 0.0, "dual_holds": False})
                feedback = iter_log
        else:
            # heuristic score: identifier overlap with ψ
            key_ids = set((psi.get("A_plus") or []) + (psi.get("A_minus") or []))
            ql_ids = set(re.findall(r"\b\w+\b", ql_text))
            coverage = len(key_ids & ql_ids) / max(len(key_ids), 1)
            score = 0.3 + 0.4 * coverage
            iter_log.update(
                {
                    "score": round(score, 4),
                    "precision": round(coverage, 4),
                    "recall": round(coverage, 4),
                    "dual": 0.0,
                    "dual_holds": False,
                    "note": "heuristic_score_no_dual_db",
                }
            )
            feedback = iter_log

        validation_basis = "dual_db" if dual_available else "heuristic_no_dual_db"
        score_passes = float(iter_log.get("score", 0.0)) >= SCORE_THRESHOLD
        rule_validated = bool(
            score_passes
            and (
                not dual_available
                or (
                    iter_log.get("db_before_ok")
                    and iter_log.get("db_after_ok")
                    and iter_log.get("dual_holds")
                )
            )
        )
        iter_log["rule_validation_basis"] = validation_basis
        iter_log["rule_validated"] = rule_validated
        if candidate_rule is not None:
            candidate_rule["validated"] = rule_validated
            candidate_rule["validation"] = {
                key: iter_log[key]
                for key in (
                    "compiled",
                    "score",
                    "precision",
                    "recall",
                    "dual",
                    "dual_holds",
                    "db_before_ok",
                    "db_after_ok",
                )
                if key in iter_log
            }
            candidate_rule["validation"]["status"] = (
                "validated" if rule_validated else "rejected"
            )
            candidate_rule["validation"]["basis"] = validation_basis
            candidate_rule["validation"]["score_threshold"] = SCORE_THRESHOLD
            if rule_validated:
                if fix_id and not replaced_current_fix:
                    # Replace this fix's historical records only after the rerun
                    # has produced a newly validated rule. Failed/legacy reruns
                    # therefore retain the last usable IR.
                    prior_candidates = [
                        candidate
                        for candidate in prior_candidates
                        if str(candidate.get("fix_id") or "") != fix_id
                    ]
                    rule_candidates = prior_candidates + current_rule_candidates
                    validated_rules = [
                        candidate
                        for candidate in validated_rules
                        if str(candidate.get("fix_id") or "") != fix_id
                    ]
                    replaced_current_fix = True
                validated_rules.append(candidate_rule)
                deduplicated_rules = deduplicate_validated_rules(validated_rules)
            write_rule_ir(rule_candidates, deduplicated_rules)

        synthesis_log["iterations"].append(iter_log)
        prev_ql = ql_text

        if float(iter_log.get("score", 0.0)) > best_score:
            best_score = float(iter_log["score"])
            best_ql = ql_text

        if float(iter_log.get("score", 0.0)) >= SCORE_THRESHOLD and bool(iter_log.get("dual_holds")):
            synthesis_log["convergence"] = True
            break

    if best_ql:
        QL_OUT.write_text(best_ql + "\n", encoding="utf-8")

    deduplicated_rules = deduplicate_validated_rules(validated_rules)
    synthesis_log["validated_rules"] = deduplicated_rules
    write_rule_ir(rule_candidates, deduplicated_rules)
    synthesis_log["final_score"] = round(max(best_score, 0.0), 4)
    SYNTH_LOG.write_text(json.dumps(synthesis_log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return synthesis_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dbms", default=os.environ.get("SQLEEK_DBMS", "postgres").lower())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_dbms(args.dbms)
    log = pgqs()
    print(f"[pgqs] wrote {SYNTH_LOG} (mode={log.get('mode')}, final_score={log.get('final_score')})")


if __name__ == "__main__":
    main()
