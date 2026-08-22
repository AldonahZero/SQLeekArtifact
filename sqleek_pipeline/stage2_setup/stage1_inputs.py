"""Load Stage 1 artifacts consumed by Stage 2."""
from __future__ import annotations

import json
from typing import Any

from config import DBMS_LIST

from sqleek_pipeline.stage2_setup.common import CHAINS_JSON, PHI_JSON


def _clean_entry_map(entry_map: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for entry_fn, chain_list in entry_map.items():
        if not isinstance(entry_fn, str) or not isinstance(chain_list, list):
            continue
        cleaned: list[dict[str, Any]] = [
            c for c in chain_list if isinstance(c, dict) and c.get("danger_fn") and c.get("depth")
        ]
        if cleaned:
            out[entry_fn] = cleaned
    return out


def _entry_map_from_chain_rows(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = row.get("entry")
        target = row.get("target") or row.get("danger_fn")
        depth = row.get("depth")
        if not entry or not target or not depth:
            continue
        out.setdefault(str(entry), []).append(
            {
                "danger_fn": str(target),
                "depth": depth,
                "path": row.get("functions") or row.get("path") or [entry, target],
            }
        )
    return _clean_entry_map(out)


def load_callchains() -> dict[str, dict[str, list[dict[str, Any]]]]:
    if not CHAINS_JSON.exists():
        raise FileNotFoundError(f"missing Stage 1 output: {CHAINS_JSON}")
    payload = json.loads(CHAINS_JSON.read_text(encoding="utf-8"))

    # Shape A: per-DBMS map
    #   { "postgres": { entry_fn: [ ... ] }, "sqlite": { ... }, ... }
    if isinstance(payload, dict) and any(k in payload for k in DBMS_LIST):
        by_dbms: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for dbms in DBMS_LIST:
            v = payload.get(dbms)
            if isinstance(v, dict):
                cleaned = _clean_entry_map(v)
            elif isinstance(v, list):
                cleaned = _entry_map_from_chain_rows(v)
            else:
                cleaned = {}
                if cleaned:
                    by_dbms[dbms] = cleaned
        if by_dbms:
            return by_dbms

    # Shape B (per user task): single DBMS (historically postgres)
    #   { entry_fn: [ {danger_fn, depth, ...}, ... ] }
    if isinstance(payload, dict) and payload and all(isinstance(v, list) for v in payload.values()):
        cleaned = _clean_entry_map(payload)  # type: ignore[arg-type]
        if cleaned:
            return {"postgres": cleaned}

    # Stage 1 robust shape:
    #   { "by_entry": { EntryFn: [ {danger_fn, depth, path}, ... ] }, ... }
    if isinstance(payload, dict) and isinstance(payload.get("by_entry"), dict):
        cleaned = _clean_entry_map(payload["by_entry"])  # type: ignore[arg-type]
        if cleaned:
            active_dbms = payload.get("active_dbms")
            if isinstance(active_dbms, str) and active_dbms in DBMS_LIST:
                return {active_dbms: cleaned}
            return {"postgres": cleaned}

    raise ValueError(f"unrecognized callchains.json shape in {CHAINS_JSON}")


def load_phi_mapping() -> dict[str, set[str]]:
    """Load Stage 1 Φ mapping: function name -> clause tags (uppercased)."""
    if not PHI_JSON.exists():
        return {}
    try:
        payload = json.loads(PHI_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

    phi_map: dict[str, set[str]] = {}
    raw = payload.get("phi_mapping") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    for fn, tags in raw.items():
        if not isinstance(fn, str) or not fn:
            continue
        if not isinstance(tags, list):
            continue
        cleaned = {str(t).strip().upper() for t in tags if str(t).strip()}
        cleaned.discard("UNKNOWN")
        if cleaned:
            phi_map[fn] = cleaned
    return phi_map
