"""Stage 2 seed generation orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config import BUG_TYPES, DBMS_LIST
from sqleek_pipeline.llm import OpenAILLMClient
from sqleek_pipeline.stage2_setup.common import (
    CHAINS_JSON,
    OUTPUT_DIR,
    PHI_JSON,
    SEEDS_DIR,
    default_source_root,
    log,
)
from sqleek_pipeline.stage2_setup.llm_inference import infer_sql_template, repair_sql_candidate
from sqleek_pipeline.stage2_setup.seed_scoring import (
    compute_seed_weight,
    expand_template,
    softmax,
    write_seed,
)
from sqleek_pipeline.stage2_setup.seed_validation import (
    MAX_REPAIR_ROUNDS,
    load_executor_config,
    validate_and_repair_seed,
)
from sqleek_pipeline.stage2_setup.source_context import (
    get_source_context,
    load_hotspot_contexts,
    top_functions_for_context,
)
from sqleek_pipeline.stage2_setup.stage1_inputs import load_callchains, load_phi_mapping


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    # Append a session header to root build.log
    log("===== Stage 2 start (llm_inferred) =====")

    chains_by_dbms = load_callchains()
    phi_map = load_phi_mapping()

    max_entries = int(os.environ.get("STAGE2_MAX_ENTRIES", "10"))
    top_templates = int(os.environ.get("STAGE2_TOP_TEMPLATES", "5"))
    variants_per_template = int(os.environ.get("STAGE2_VARIANTS_PER_TEMPLATE", "10"))
    requested_dbms = {
        item.strip().lower()
        for item in os.environ.get("SQLEEK_LLM_DBMS", "").split(",")
        if item.strip()
    }
    active_dbms = [dbms for dbms in DBMS_LIST if not requested_dbms or dbms in requested_dbms]

    client = OpenAILLMClient()

    generation_log: dict[str, Any] = {
        "mode": "llm_inferred",
        "chains_path": str(CHAINS_JSON),
        "phi_mapping_path": str(PHI_JSON),
        "max_entries": max_entries,
        "top_templates_expanded": top_templates,
        "variants_per_template": variants_per_template,
        "dbms_filter": active_dbms if requested_dbms else None,
        "model": os.environ.get("OPENAI_MODEL", ""),
        "usage_log": os.environ.get("SQLEEK_LLM_USAGE_LOG", ""),
        "templates": [],
        "total_seeds": 0,
    }

    all_templates = _infer_templates(
        chains_by_dbms,
        phi_map,
        generation_log,
        client,
        max_entries,
        dbms_list=active_dbms,
    )
    seed_count, validation_records = _write_ranked_seeds(
        all_templates,
        top_templates,
        variants_per_template,
        SEEDS_DIR,
        client,
    )

    generation_log["templates"] = all_templates
    generation_log["total_seeds"] = seed_count
    generation_log["validation"] = {
        "enabled": True,
        "max_repair_rounds": MAX_REPAIR_ROUNDS,
        "attempted": len(validation_records),
        "validated": sum(1 for record in validation_records if record["validation"].get("validated")),
        "rejected": sum(1 for record in validation_records if not record["validation"].get("validated")),
        "records": validation_records,
    }

    log_path = OUTPUT_DIR / "seed_generation_stage2.json"
    log_path.write_text(json.dumps(generation_log, indent=2), encoding="utf-8")

    if all_templates:
        top = all_templates[0]
        log(f"Stage 2 complete: templates={len(all_templates)} seeds={seed_count} top_weight={top.get('seed_weight')}")
        log(f"top_clauses={top.get('clauses')}")
    else:
        log("Stage 2 complete: no templates generated (empty callchains?)")

    log("===== Stage 2 end =====")


def _infer_templates(
    chains_by_dbms: dict[str, dict[str, list[dict[str, Any]]]],
    phi_map: dict[str, set[str]],
    generation_log: dict[str, Any],
    client: OpenAILLMClient,
    max_entries: int,
    dbms_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    all_templates: list[dict[str, Any]] = []

    for dbms in dbms_list or DBMS_LIST:
        entry_map = chains_by_dbms.get(dbms) or {}
        if not entry_map:
            log(f"skip dbms={dbms}: no callchains")
            continue

        src_root = Path(os.environ.get(f"SQLEEK_{dbms.upper()}_SRC", default_source_root(dbms)))
        generation_log.setdefault("src_roots", {})[dbms] = str(src_root)

        # Deterministic order to make runs comparable
        entry_items = sorted(entry_map.items(), key=lambda kv: kv[0])
        entry_items = entry_items[: max_entries if max_entries > 0 else len(entry_items)]

        for entry_fn, chain_list in entry_items:
            if not chain_list:
                continue

            bug_type = _select_bug_type(chain_list)
            log(f"processing dbms={dbms} bug_type={bug_type} entry_fn={entry_fn} chain_len={len(chain_list)}")

            top_fns = top_functions_for_context(chain_list, k=3)
            source_contexts = _collect_source_contexts(dbms, bug_type, chain_list, src_root)
            phi_hints = _collect_phi_hints(entry_fn, chain_list, phi_map)

            result = infer_sql_template(client, dbms, entry_fn, chain_list, source_contexts, phi_hints)
            weight = compute_seed_weight(result, chain_list)

            result["seed_weight"] = weight
            result["entry_fn"] = entry_fn
            result["dbms"] = dbms
            result["bug_type"] = bug_type
            result["top_functions"] = top_fns
            result["phi_hints"] = phi_hints
            all_templates.append(result)

            log(
                "template inferred "
                f"(confidence={result.get('confidence')}, weight={weight}) "
                f"clauses={result.get('clauses')}"
            )

    all_templates.sort(key=lambda x: float(x.get("seed_weight") or 0.0), reverse=True)

    weights = [float(t.get("seed_weight") or 0.0) for t in all_templates]
    probs = softmax(weights)
    for t, p in zip(all_templates, probs):
        t["softmax_prob"] = round(float(p), 4)

    return all_templates


def _collect_source_contexts(
    dbms: str,
    bug_type: str,
    chain_list: list[dict[str, Any]],
    src_root: Path,
) -> dict[str, str]:
    top_fns = top_functions_for_context(chain_list, k=3)
    source_contexts: dict[str, str] = {}
    for fn in top_fns:
        source_contexts[fn] = get_source_context(fn, src_root)

    # Strengthen LLM evidence with Stage 1 hotspot locations (e.g., typcache.c:...).
    # This is still sourced from Stage 1 static analysis targets, not known-bug knowledge.
    hotspot_ctx = load_hotspot_contexts(dbms, bug_type, src_root, max_hotspots=8)
    if hotspot_ctx:
        source_contexts.update(hotspot_ctx)
    return source_contexts


def _collect_phi_hints(
    entry_fn: str,
    chain_list: list[dict[str, Any]],
    phi_map: dict[str, set[str]],
) -> list[str]:
    phi_tags: set[str] = set()
    phi_tags |= phi_map.get(entry_fn, set())
    for c in chain_list:
        fn = c.get("danger_fn")
        if isinstance(fn, str):
            phi_tags |= phi_map.get(fn, set())
    return sorted(phi_tags)


def _select_bug_type(chain_list: list[dict[str, Any]]) -> str:
    for c in chain_list:
        bt = c.get("bug_type")
        if isinstance(bt, str) and bt in BUG_TYPES:
            return bt
    return "memory"


def _write_ranked_seeds(
    all_templates: list[dict[str, Any]],
    top_templates: int,
    variants_per_template: int,
    out_base: Path,
    client: OpenAILLMClient,
) -> tuple[int, list[dict[str, Any]]]:
    seed_count = 0
    validation_records: list[dict[str, Any]] = []
    executor_by_dbms: dict[str, Any] = {}
    for rank, tmpl in enumerate(all_templates[:top_templates]):
        template_str = str(tmpl.get("template") or "SELECT 1;")
        variants = expand_template(template_str, k=variants_per_template)
        dbms = str(tmpl.get("dbms") or "unknown")
        bug_type = str(tmpl.get("bug_type") or "memory")
        if dbms not in executor_by_dbms:
            executor_by_dbms[dbms] = load_executor_config(dbms)
        executor = executor_by_dbms[dbms]
        seed_dir = out_base / dbms / bug_type
        seed_dir.mkdir(parents=True, exist_ok=True)
        for vi, variant in enumerate(variants):
            seed_file = seed_dir / f"llm_rank{rank:02d}_{vi:02d}.sql"
            candidate_id = f"{dbms}:{bug_type}:rank{rank:02d}_variant{vi:02d}"
            validation = validate_and_repair_seed(
                variant,
                dbms=dbms,
                client=client,
                repair_fn=repair_sql_candidate,
                executor=executor,
                max_repair_rounds=MAX_REPAIR_ROUNDS,
                candidate_id=candidate_id,
                context={
                    "entry_fn": tmpl.get("entry_fn", ""),
                    "clauses": tmpl.get("clauses", []),
                    "reasoning": tmpl.get("reasoning", ""),
                    "risk_scenario": tmpl.get("risk_scenario", ""),
                },
            )
            validation_records.append(
                {
                    "candidate_id": candidate_id,
                    "dbms": dbms,
                    "bug_type": bug_type,
                    "rank": rank,
                    "variant": vi,
                    "seed_file": str(seed_file),
                    "validation": validation,
                }
            )
            # Rejected candidates never reach the seed directory writer.
            if validation.get("validated"):
                write_seed(seed_file, str(validation.get("sql") or variant))
                seed_count += 1
            elif seed_file.exists():
                # Do not leave a stale generated seed that failed this run's
                # validation in the corpus consumed by Stage 3.
                seed_file.unlink()
    return seed_count, validation_records
