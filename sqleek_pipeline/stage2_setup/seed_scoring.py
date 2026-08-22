"""Seed ranking and template expansion helpers."""
from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any


def compute_seed_weight(template_result: dict[str, Any], chain: list[dict[str, Any]]) -> float:
    """SeedWeight heuristic using only inferred clauses + depth + confidence."""
    clauses = template_result.get("clauses") or []
    clause_set = {str(c).upper() for c in clauses if str(c).strip()}
    if not clause_set:
        return 0.0

    score = 0.0
    for c in chain:
        depth = int(c.get("depth") or 999)
        depth = max(depth, 1)
        score += 1.0 / depth

    conf = template_result.get("confidence", 0.5)
    try:
        conf_f = float(conf)
    except Exception:
        conf_f = 0.5

    return round(score * max(0.0, min(1.0, conf_f)), 4)


def softmax(weights: list[float]) -> list[float]:
    if not weights:
        return []
    m = max(weights)
    exps = [math.exp(w - m) for w in weights]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


def expand_template(template_str: str, k: int = 10) -> list[str]:
    """Expand(s, k): generate variants by substituting template fields with interesting values."""
    param_grid: dict[str, list[str]] = {
        "type_name": ["foo", "mytype", "cursor_rowtype"],
        "type_a": ["INT", "BIGINT"],
        "type_b": ["INT", "REAL", "BIGINT"],
        "new_type": ["TEXT", "VARCHAR(100)", "BYTEA"],
        "n": ["2", "10", "100"],
        "expr": ["power(2, 30)", "i * 1000000", "2147483647"],
    }

    variants: list[str] = []
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    for combo in itertools.product(*values):
        if len(variants) >= k:
            break
        params = dict(zip(keys, combo))
        v = template_str
        for key, val in params.items():
            v = v.replace("{" + key + "}", val)
        if "{" in v or "}" in v:
            continue
        variants.append(v.rstrip() + "\n")

    if not variants:
        variants = ["SELECT 1;\n"]
    while len(variants) < k:
        variants.append(variants[0])
    return variants[:k]


def write_seed(path: Path, sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql.rstrip() + "\n", encoding="utf-8")
