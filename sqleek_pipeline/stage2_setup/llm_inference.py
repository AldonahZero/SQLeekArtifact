"""LLM prompt rendering and response parsing for Stage 2."""
from __future__ import annotations

import json
from typing import Any

try:
    import json_repair  # type: ignore
except Exception:  # pragma: no cover
    json_repair = None  # type: ignore[assignment]


def infer_sql_template(
    client: Any,
    dbms: str,
    entry_fn: str,
    chain: list[dict[str, Any]],
    source_contexts: dict[str, str],
    phi_hints: list[str],
) -> dict[str, Any]:
    """Core LLM inference: callchain + source contexts -> SQL template + clauses."""
    chain_sorted = sorted(chain, key=lambda x: int(x.get("depth") or 999))
    chain_str = " →\n  ".join(f"{c.get('danger_fn')} (depth={c.get('depth')})" for c in chain_sorted)

    ctx_str = "\n\n".join(
        f"=== {fn} ===\n{ctx}" for fn, ctx in source_contexts.items() if isinstance(ctx, str) and ctx.strip()
    )

    phi_hint_str = ", ".join(phi_hints) if phi_hints else "NONE"

    try:
        # Keep the full protocol in the skill file, and render template fields here
        # so the prompt stays centralized under sqleek_pipeline/llm/skills/.
        skill_template = client.load_skill("seed_infer")
        system_prompt = (
            skill_template.replace("{{dbms}}", dbms)
            .replace("{{entry_fn}}", entry_fn)
            .replace("{{chain_str}}", chain_str)
            .replace("{{source_context}}", ctx_str)
            .replace("{{phi_hints}}", phi_hint_str)
        )
        text = client.complete(system_prompt, "", temperature=0.2)
    except Exception as e:
        return {
            "template": "SELECT 1;",
            "clauses": [],
            "reasoning": f"LLM request failed: {e}",
            "confidence": 0.0,
            "risk_scenario": "",
        }

    text = _strip_code_fences(text)
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("LLM JSON is not an object")
        return obj
    except Exception:
        if json_repair is not None:
            try:
                repaired = json_repair.repair_json(text)
                obj = json.loads(repaired)
                if isinstance(obj, dict):
                    obj.setdefault("risk_scenario", "")
                    obj["risk_scenario"] = f"{obj['risk_scenario']}\njson_repair_applied=true".strip()
                    return obj
            except Exception:
                pass
        return {
            "template": "SELECT 1;",
            "clauses": [],
            "reasoning": "LLM parse failed",
            "confidence": 0.0,
            "risk_scenario": f"raw_response={text[:400]}",
        }


def _strip_code_fences(text: str) -> str:
    if "```" not in text:
        return text.strip()
    parts = text.split("```")
    if len(parts) < 2:
        return text.strip()
    mid = parts[1]
    if mid.startswith("json\n"):
        mid = mid[5:]
    return mid.strip()
