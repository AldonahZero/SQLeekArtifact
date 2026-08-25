#!/usr/bin/env python3
"""Equivalence and deduplication for validated structured rule IRs.

Only the structured ``predicates`` field participates in equivalence.  Query
text, fix/commit metadata, scores, and other provenance fields are deliberately
ignored.  A local variable must be represented explicitly as
``{"var": "name"}``; ordinary strings remain semantic predicate/API names or
constants and are never alpha-renamed.

Example predicate IR::

    [
        ["dataDependent", {"var": "i"}, {"var": "a"}],
        ["sameObject", {"var": "a"}, {"var": "o"}],
        ["not", "validated", {"var": "i"}],
        ["reachesRiskOperation", {"var": "a"}],
    ]

Top-level predicates are an AND-connected conjunction, so their order has no
meaning.  Rules are never merged across DBMSes.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from itertools import permutations
from typing import Any, Iterable


# Exact alpha-canonicalization is factorial in the number of local variables.
# PGQS rules are intentionally small; refusing an unexpectedly large IR is
# safer than silently producing an order-dependent or approximate signature.
MAX_EXACT_VARIABLES = 8


def _is_local_variable(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"var"}
        and isinstance(value["var"], str)
        and bool(value["var"])
    )


def _collect_variables(value: Any, variables: set[str]) -> None:
    if _is_local_variable(value):
        variables.add(value["var"])
        return
    if isinstance(value, list):
        for item in value:
            _collect_variables(item, variables)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_variables(item, variables)


def _rename_variables(value: Any, variable_map: dict[str, str]) -> Any:
    if _is_local_variable(value):
        return {"var": variable_map[value["var"]]}
    if isinstance(value, list):
        return [_rename_variables(item, variable_map) for item in value]
    if isinstance(value, dict):
        return {
            key: _rename_variables(item, variable_map)
            for key, item in value.items()
        }
    return value


def _validate_variable_tags(value: Any, location: str) -> None:
    if isinstance(value, dict):
        if "var" in value and not _is_local_variable(value):
            raise ValueError(
                f"{location} has an invalid local variable; expected exactly "
                '{"var": "non-empty-name"}'
            )
        for key, item in value.items():
            _validate_variable_tags(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_variable_tags(item, f"{location}[{index}]")


def _validated_predicates(rule: dict[str, Any]) -> list[list[Any]]:
    predicates = rule.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        raise ValueError("rule predicates must be a non-empty list")

    for index, predicate in enumerate(predicates):
        if not isinstance(predicate, list) or not predicate:
            raise ValueError(f"predicate {index} must be a non-empty list")
        if not isinstance(predicate[0], str) or not predicate[0]:
            raise ValueError(f"predicate {index} must start with a predicate name")
        _validate_variable_tags(predicate[1:], f"predicate {index} arguments")

    try:
        json.dumps(predicates, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("rule predicates must be JSON-serializable") from exc
    return predicates


def canonicalize_rule(
    rule: dict[str, Any],
    *,
    max_exact_variables: int = MAX_EXACT_VARIABLES,
) -> str:
    """Return the exact alpha- and order-canonical predicate representation.

    Enumerating variable mappings is deliberately used here instead of assigning
    names on first occurrence.  First-occurrence naming depends on the original
    predicate order and therefore gives different signatures for some reordered
    but equivalent conjunctions.
    """

    predicates = _validated_predicates(rule)
    variables: set[str] = set()
    for predicate in predicates:
        # The first element is the semantic predicate name, never a variable.
        _collect_variables(predicate[1:], variables)

    ordered_variables = sorted(variables)
    if len(ordered_variables) > max_exact_variables:
        raise ValueError(
            "rule has "
            f"{len(ordered_variables)} local variables; exact canonicalization "
            f"supports at most {max_exact_variables}"
        )

    canonical_names = [f"v{index}" for index in range(len(ordered_variables))]
    target_orders: Iterable[tuple[str, ...]]
    if canonical_names:
        target_orders = permutations(canonical_names)
    else:
        target_orders = [tuple()]

    best: str | None = None
    for target_order in target_orders:
        variable_map = dict(zip(ordered_variables, target_order))
        normalized_predicates = [
            _rename_variables(predicate, variable_map)
            for predicate in predicates
        ]
        normalized_predicates.sort(
            key=lambda predicate: json.dumps(
                predicate,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        candidate = json.dumps(
            normalized_predicates,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if best is None or candidate < best:
            best = candidate

    # target_orders always contains at least the empty tuple.
    assert best is not None
    return best


def rule_signature(rule: dict[str, Any]) -> str:
    canonical = canonicalize_rule(rule)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deduplicate_validated_rules(rules: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group equivalent validated rules and retain one executable representative."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    group_order: list[tuple[str, str]] = []

    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("each rule must be an object")
        dbms = str(rule.get("dbms") or "").strip().lower()
        if not dbms:
            raise ValueError("each rule must have a dbms")

        signature = rule_signature(rule)
        key = (dbms, signature)
        if key not in groups:
            group_order.append(key)
        groups[key].append(rule)

    deduplicated: list[dict[str, Any]] = []
    for dbms, signature in group_order:
        equivalent_rules = groups[(dbms, signature)]
        supporting_fix_set: set[str] = set()
        for rule in equivalent_rules:
            if rule.get("fix_id") not in (None, ""):
                supporting_fix_set.add(str(rule["fix_id"]))
            prior_support = rule.get("supporting_fixes")
            if isinstance(prior_support, list):
                supporting_fix_set.update(
                    str(fix_id)
                    for fix_id in prior_support
                    if fix_id not in (None, "")
                )
        supporting_fixes = sorted(supporting_fix_set)

        representative = deepcopy(equivalent_rules[0])
        representative["dbms"] = dbms
        representative["rule_signature"] = signature
        representative["supporting_fixes"] = supporting_fixes
        representative["support_count"] = len(supporting_fixes)
        deduplicated.append(representative)

    return deduplicated
