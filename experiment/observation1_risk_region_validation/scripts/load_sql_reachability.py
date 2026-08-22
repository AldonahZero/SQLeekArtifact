#!/usr/bin/env python3
"""Attach Stage 1 SQL-entry reachability metadata to risk regions."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common import (
    canonical_dbms,
    data_dir,
    git,
    int_or_zero,
    load_config,
    normalize_path,
    parse_codeql_csv,
    read_csv,
    safe_div,
    stage1_dbms,
    write_csv,
)


CHAIN_RE = re.compile(
    r"depth=(?P<depth>\d+)\s+(?P<entry>[A-Za-z_][A-Za-z0-9_:]*)\s+(?:→\*|->\*)\s+(?P<target>[A-Za-z_][A-Za-z0-9_:]*)"
)


def overlaps(region: dict[str, str], file_path: str, start: int, end: int) -> bool:
    if normalize_path(region["file_path"]) != normalize_path(file_path):
        return False
    r1 = int_or_zero(region["start_line"])
    r2 = int_or_zero(region["end_line"], r1)
    return max(r1, start) <= min(r2, end)


def parse_chain_message(message: str) -> list[dict[str, object]]:
    out = []
    for match in CHAIN_RE.finditer(message or ""):
        out.append(
            {
                "entry": match.group("entry"),
                "target": match.group("target"),
                "depth": int(match.group("depth")),
                "path": f"{match.group('entry')}->{match.group('target')}",
                "source": "stage1_dbms_callchain_csv",
            }
        )
    return out


def load_chain_json(targets_dir: Path, dbms: str) -> list[dict[str, object]]:
    path = targets_dir / "callchains.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    key = stage1_dbms(dbms)
    chains = data.get(key, [])
    out = []
    if isinstance(chains, list):
        for item in chains:
            target = item.get("target") or item.get("danger_fn")
            entry = item.get("entry")
            if not target or not entry:
                continue
            out.append(
                {
                    "entry": str(entry),
                    "target": str(target),
                    "depth": int(item.get("depth") or 1),
                    "path": "->".join(str(x) for x in (item.get("functions") or item.get("path") or [entry, target])),
                    "source": "stage1_targets_callchains_json",
                }
            )
    return out


def build_reachability(dbms: str) -> list[dict[str, object]]:
    cfg = load_config(dbms)
    repo = Path(str(cfg["source_repo"]))
    reach_commit = git(repo, "rev-parse", "HEAD").strip()
    regions = read_csv(data_dir(dbms) / "risk_regions.csv")
    stage_dir = Path(str(cfg["stage1_codeql_results_dir"]))
    targets_dir = Path(str(cfg["stage1_targets_dir"]))

    csv_rows = parse_codeql_csv(stage_dir / "dbms_callchain.csv")
    chains = load_chain_json(targets_dir, dbms)
    location_hits: dict[str, list[dict[str, object]]] = {r["region_id"]: [] for r in regions}
    for row in csv_rows:
        for chain in parse_chain_message(str(row.get("message", ""))):
            chains.append(chain)
        for region in regions:
            if overlaps(
                region,
                str(row.get("file_path", "")),
                int_or_zero(row.get("start_line")),
                int_or_zero(row.get("end_line"), int_or_zero(row.get("start_line"))),
            ):
                for chain in parse_chain_message(str(row.get("message", ""))):
                    location_hits[region["region_id"]].append(chain)

    by_target: dict[str, list[dict[str, object]]] = {}
    for chain in chains:
        target = str(chain.get("target") or "").split("::")[-1]
        by_target.setdefault(target, []).append(chain)

    rows = []
    for region in regions:
        fn = (region.get("enclosing_function") or "").split("::")[-1]
        hits = []
        if fn and fn in by_target:
            hits.extend(by_target[fn])
        hits.extend(location_hits.get(region["region_id"], []))
        # Also allow direct mention of the target function in the Stage 1 alert.
        msg = region.get("alert_message") or ""
        for target, target_hits in by_target.items():
            if target and re.search(rf"\b{re.escape(target)}\b", msg):
                hits.extend(target_hits)
        # Deduplicate while preserving shortest paths.
        uniq = {}
        for hit in hits:
            key = (hit.get("entry"), hit.get("target"), hit.get("depth"))
            uniq[key] = hit
        hits = list(uniq.values())
        if hits:
            best = min(hits, key=lambda h: int(h.get("depth") or 999999))
            rows.append(
                {
                    "dbms": canonical_dbms(dbms),
                    "region_id": region["region_id"],
                    "sql_reachable": True,
                    "sql_entry_count": len(set(str(h.get("entry")) for h in hits)),
                    "nearest_sql_entry": best.get("entry", ""),
                    "shortest_call_distance": best.get("depth", ""),
                    "callchain_count": len(hits),
                    "reachability_method": best.get("source", "stage1_sql_entry_callchain"),
                    "reachability_commit": reach_commit,
                    "callchain_targets": ";".join(sorted(set(str(h.get("target")) for h in hits))),
                }
            )
        else:
            rows.append(
                {
                    "dbms": canonical_dbms(dbms),
                    "region_id": region["region_id"],
                    "sql_reachable": False,
                    "sql_entry_count": 0,
                    "nearest_sql_entry": "",
                    "shortest_call_distance": "",
                    "callchain_count": 0,
                    "reachability_method": "not_found_in_stage1_sql_entry_callchains",
                    "reachability_commit": reach_commit,
                    "callchain_targets": "",
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dbms = canonical_dbms(args.dbms)
    out = data_dir(dbms) / "sql_reachability.csv"
    if out.is_file() and not args.force:
        print(out)
        return
    rows = build_reachability(dbms)
    write_csv(out, rows)
    print(f"{out} reachable={sum(1 for r in rows if r['sql_reachable'])}/{len(rows)}")


if __name__ == "__main__":
    main()
