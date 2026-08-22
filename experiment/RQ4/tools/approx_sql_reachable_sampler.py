#!/usr/bin/env python3
"""Demand-driven approximate SQL-reachability sampler for RQ4 w/o-M1.

The exact pool query materializes the global ``entry.calls+(function)``
relation.  For very large DBMSs this is needlessly expensive because w/o-M1
only needs K uniformly proposed source functions, not the complete relation.

This tool first enumerates the source-function universe (no transitive
closure), then proposes functions uniformly without replacement and performs
a bounded, demand-driven reverse call-graph search from the proposed targets
to SQL entry points.  A target proven reachable by the bounded search is
accepted.  With an exact reverse search this is rejection sampling from the
true reachable pool; with the node/depth caps it is an auditable approximation
to that distribution.

The downstream artifacts intentionally match prepare_wo_m1.py: K target
anchors, callchains.json, phi_mapping.json, weights.json, and MANIFEST.json.
M2 and M3 can therefore consume this isolated target root unchanged.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path("/root/SQLeek")
TOOLS = ROOT / "experiment/RQ4/tools"
DEFAULT_DB_ROOT = ROOT / "sqleek_pipeline/stage1_static/output/codeql_db"
DEFAULT_CODEQL = "/root/codeql/codeql"
# Match prepare_wo_m1.py and the existing Stage-1 invocations.  The bundled
# CodeQL packs live outside the SQLeek checkout on the remote server.
DEFAULT_SEARCH_PATH = Path("/root/codeql/qlpacks")
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx"}
NON_SOURCE_PARTS = {
    "build", "builds", "generated", "third_party", "vendor", "client",
    "clients", "extra", "test", "tests", "unittest", "examples",
    "example", "bench", "benchmark", "doc", "docs", "scripts",
    "support-files", "packaging", "debian", "win", "windows",
}
SQL_ENTRY_PATTERNS = (
    "exec_simple_query", "PortalRun", "PortalRunSelect", "ExecutorRun",
    "ExecProcNode", "ExecFetch", "RunFromStore", "printtup",
    "OutputFunctionCall", "record_out", "textout", "sqlite3_exec",
    "sqlite3_prepare", "sqlite3_step", "sqlite3VdbeExec",
    "handle_connection", "do_command", "dispatch_command", "mysql_parse",
    "mysql_execute_command", "mysql_execute", "monetdbe_query",
    "monetdbe_query_internal", "monetdbe_query_remote", "SQLengine",
    "SQLparser",
)
MESSAGE_RE = re.compile(
    r"entry=(?P<entry>[^;\r\n]+);"
    r"function=(?P<function>[^;\r\n]+);"
    r"file=(?P<file>[^;\r\n]+);"
    r"start=(?P<start>\d+);"
    r"end=(?P<end>\d+);"
    r"depth=(?P<depth>\d+)"
)
EDGE_RE = re.compile(
    r"callee_function=(?P<callee_function>[^;\r\n]+);"
    r"callee_file=(?P<callee_file>[^;\r\n]+);"
    r"callee_start=(?P<callee_start>\d+);"
    r"callee_end=(?P<callee_end>\d+);"
    r"caller_function=(?P<caller_function>[^;\r\n]+);"
    r"caller_file=(?P<caller_file>[^;\r\n]+);"
    r"caller_start=(?P<caller_start>\d+);"
    r"caller_end=(?P<caller_end>\d+)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def key(item: dict[str, Any]) -> tuple[str, str, int, int]:
    return (
        str(item["function"]),
        str(item["file"]),
        int(item["start_line"]),
        int(item["end_line"]),
    )


def key_text(item: dict[str, Any]) -> str:
    fn, file, start, end = key(item)
    return f"{fn}|{file}|{start}|{end}"


def valid_candidate(item: dict[str, Any]) -> bool:
    path = Path(str(item["file"]))
    start = int(item["start_line"])
    end = int(item["end_line"])
    return (
        bool(item["function"])
        and bool(item["entry"])
        and start > 0
        and end >= start
        and path.suffix.lower() in SOURCE_EXTENSIONS
        and not any(part.lower() in NON_SOURCE_PARTS for part in path.parts)
    )


def parse_universe_csv(path: Path) -> tuple[list[dict[str, Any]], int]:
    csv.field_size_limit(sys.maxsize)
    rows: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    raw_rows = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        for row in csv.reader(fp):
            raw_rows += 1
            for field in row:
                for line in field.splitlines():
                    match = MESSAGE_RE.search(line.strip())
                    if not match:
                        continue
                    item = {
                        "entry": match.group("entry").strip(),
                        "function": match.group("function").strip(),
                        "file": match.group("file").strip(),
                        "start_line": int(match.group("start")),
                        "end_line": int(match.group("end")),
                        "depth": int(match.group("depth")),
                    }
                    if valid_candidate(item):
                        rows.setdefault(key(item), item)
    return sorted(rows.values(), key=key), raw_rows


def parse_edges(path: Path) -> list[dict[str, Any]]:
    csv.field_size_limit(sys.maxsize)
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        for row in csv.reader(fp):
            for field in row:
                for line in field.splitlines():
                    match = EDGE_RE.search(line.strip())
                    if not match:
                        continue
                    g = match.group
                    callee = {
                        "function": g("callee_function").strip(),
                        "file": g("callee_file").strip(),
                        "start_line": int(g("callee_start")),
                        "end_line": int(g("callee_end")),
                    }
                    caller = {
                        "function": g("caller_function").strip(),
                        "file": g("caller_file").strip(),
                        "start_line": int(g("caller_start")),
                        "end_line": int(g("caller_end")),
                    }
                    edges[(key_text(callee), key_text(caller))] = {
                        "callee": callee,
                        "caller": caller,
                    }
    return list(edges.values())


def ql_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def ql_location_clause(var: str, item: dict[str, Any]) -> str:
    return (
        f"({var}.getName() = {ql_string(item['function'])} and "
        f"{var}.getFile().getRelativePath() = {ql_string(item['file'])} and "
        f"{var}.getLocation().getStartLine() = {int(item['start_line'])} and "
        f"{var}.getLocation().getEndLine() = {int(item['end_line'])})"
    )


def universe_query() -> str:
    return r'''/**
 * @name Approximate w/o-M1 source universe
 * @description Enumerate server source functions without a transitive closure.
 * @kind problem
 * @problem.severity warning
 * @id rq4/approx-source-universe
 */
import cpp

predicate sourceCandidate(Function f) {
  f.getFile().getRelativePath().regexpMatch(".*\\.(c|cc|cpp|cxx)$") and
  not f.getFile().getRelativePath().regexpMatch(
    "(^|/)(build|builds|generated|third_party|vendor|client|clients|extra|" +
      "test|tests|unittest|examples|example|bench|benchmark|doc|docs|" +
      "scripts|support-files|packaging|debian|win|windows)(/|$)"
  )
}

from Function f
where sourceCandidate(f)
select
  f,
  "entry=source_universe" +
    ";function=" + f.getName() +
    ";file=" + f.getFile().getRelativePath() +
    ";start=" + f.getLocation().getStartLine() +
    ";end=" + f.getLocation().getEndLine() +
    ";depth=0"
'''


def ensure_work_pack(work: Path) -> None:
    """Make generated queries a real CodeQL pack.

    CodeQL needs an enclosing qlpack.yml to resolve the legacy ``cpp`` import
    and select the C/C++ dbscheme.  The generated query files live in the
    isolated output root, so they cannot inherit experiment/RQ4/tools/qlpack.yml.
    """
    pack = work / "qlpack.yml"
    pack.write_text(
        "name: sqleek/rq4-approx-work\n"
        "version: 0.0.1\n"
        "dependencies:\n"
        "  codeql/cpp-all: 9.0.0\n",
        encoding="utf-8",
    )


def reverse_edge_query(frontier: list[dict[str, Any]], query_id: str) -> str:
    clauses = " or\n  ".join(ql_location_clause("callee", item) for item in frontier)
    return f'''/**
 * @name Approximate w/o-M1 reverse direct-call frontier
 * @description Enumerate direct callers of a demand-driven frontier.
 * @kind problem
 * @problem.severity warning
 * @id rq4/approx-reverse-frontier-{query_id}
 */
import cpp

predicate frontier(Function callee) {{
  {clauses}
}}

from Function caller, Function callee
where frontier(callee) and caller.calls(callee)
select
  callee,
  "callee_function=" + callee.getName() +
    ";callee_file=" + callee.getFile().getRelativePath() +
    ";callee_start=" + callee.getLocation().getStartLine() +
    ";callee_end=" + callee.getLocation().getEndLine() +
    ";caller_function=" + caller.getName() +
    ";caller_file=" + caller.getFile().getRelativePath() +
    ";caller_start=" + caller.getLocation().getStartLine() +
    ";caller_end=" + caller.getLocation().getEndLine()
'''


def run_codeql(
    codeql: str,
    database: Path,
    query: Path,
    output: Path,
    search_path: Path,
    threads: int,
    timeout: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CODEQL_ALLOW_INSTALLATION_ANYWHERE"] = "true"
    command = [
        codeql,
        "database",
        "analyze",
        f"--search-path={search_path}",
        "--format=csv",
        f"--output={output}",
        "--rerun",
        f"--threads={threads}",
        str(database),
        str(query),
    ]
    print(f"[approx_wo_m1] {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, env=env, timeout=timeout)


def is_sql_entry(name: str) -> bool:
    return any(pattern in name for pattern in SQL_ENTRY_PATTERNS)


def target_line(item: dict[str, Any]) -> str:
    return f"{Path(str(item['file'])).as_posix()}:{int(item['start_line'])}-{int(item['end_line'])}"


def callchain_rows(selected: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(selected, key=key):
        entry = str(item["entry"])
        function = str(item["function"])
        rows.append(
            {
                "entry": entry,
                "target": function,
                "danger_fn": function,
                "depth": int(item["depth"]),
                "functions": item.get("path") or [entry, function],
                "path": item.get("path") or [entry, function],
                "source": "rq4_wo_m1_demand_driven_reverse_sampler",
                "file": str(item["file"]),
                "start_line": int(item["start_line"]),
                "end_line": int(item["end_line"]),
            }
        )
    return rows


def write_inventory(path: Path, rows: list[dict[str, Any]], full_names: set[str]) -> None:
    fields = [
        "rank", "function", "entry", "file", "start_line", "end_line",
        "depth", "target_line", "is_full_m1_name",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(sorted(rows, key=key), start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "function": row["function"],
                    "entry": row["entry"],
                    "file": row["file"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "depth": row["depth"],
                    "target_line": target_line(row),
                    "is_full_m1_name": str(row["function"]) in full_names,
                }
            )


def load_full_m1_names(dbms: str, top_k: int) -> set[str]:
    sys.path.insert(0, str(ROOT))
    from sqleek_pipeline.stage1_static.tools.gen_priority_qll import (  # type: ignore
        load_selected,
        resolve_stage0,
    )

    stage0 = resolve_stage0(dbms, None)
    if stage0 is None:
        return set()
    return set(load_selected(stage0, top_k, dbms))


class Sampler:
    def __init__(self, args: argparse.Namespace, database: Path, work: Path) -> None:
        self.args = args
        self.database = database
        self.work = work
        self.rng = random.Random(args.seed)
        self.query_index = 0
        self.query_stats: list[dict[str, Any]] = []

    def direct_callers(self, frontier: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
        if not frontier:
            return []
        self.query_index += 1
        digest = hashlib.sha1(
            (str(level) + "\n" + "\n".join(sorted(key_text(x) for x in frontier))).encode()
        ).hexdigest()[:12]
        ql_path = self.work / f"reverse_{self.query_index:04d}_{digest}.ql"
        csv_path = self.work / f"reverse_{self.query_index:04d}_{digest}.csv"
        ql_path.write_text(reverse_edge_query(frontier, digest), encoding="utf-8")
        run_codeql(
            self.args.codeql_bin,
            self.database,
            ql_path,
            csv_path,
            self.args.search_path,
            self.args.threads,
            self.args.query_timeout,
        )
        edges = parse_edges(csv_path)
        self.query_stats.append(
            {"level": level, "frontier": len(frontier), "edges": len(edges), "query": str(ql_path)}
        )
        if not self.args.keep_work:
            csv_path.unlink(missing_ok=True)
            ql_path.unlink(missing_ok=True)
        return edges

    def accept_batch(self, batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        roots = {key_text(item): item for item in batch}
        active: dict[str, set[str]] = {root: {root} for root in roots}
        frontier = {root: roots[root] for root in roots}
        seen_pairs = {(root, root) for root in roots}
        accepted: dict[str, dict[str, Any]] = {}

        for level in range(1, self.args.max_depth + 1):
            if not frontier:
                break
            frontier_items = list(frontier.values())
            if len(frontier_items) > self.args.max_frontier_nodes:
                frontier_items = self.rng.sample(frontier_items, self.args.max_frontier_nodes)
            next_nodes: dict[str, dict[str, Any]] = {}
            next_roots: dict[str, set[str]] = {}
            for start in range(0, len(frontier_items), self.args.frontier_batch):
                part = frontier_items[start : start + self.args.frontier_batch]
                edges = self.direct_callers(part, level)
                for edge in edges:
                    callee = edge["callee"]
                    caller = edge["caller"]
                    callee_id = key_text(callee)
                    caller_id = key_text(caller)
                    roots_here = active.get(callee_id, set())
                    if not roots_here:
                        continue
                    for root_id in roots_here:
                        if root_id in accepted:
                            continue
                        if is_sql_entry(str(caller["function"])) and caller_id != root_id:
                            root_item = roots[root_id]
                            accepted[root_id] = {
                                **root_item,
                                "entry": str(caller["function"]),
                                "depth": level,
                                "path": [str(caller["function"]), str(root_item["function"])],
                            }
                            continue
                        pair = (root_id, caller_id)
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        next_nodes[caller_id] = caller
                        next_roots.setdefault(caller_id, set()).add(root_id)
            if len(next_nodes) > self.args.max_frontier_nodes:
                chosen_ids = self.rng.sample(list(next_nodes), self.args.max_frontier_nodes)
                next_nodes = {node_id: next_nodes[node_id] for node_id in chosen_ids}
                next_roots = {node_id: next_roots[node_id] for node_id in chosen_ids}
            active = next_roots
            frontier = next_nodes

        return list(accepted.values()), len(roots) - len(accepted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["postgres", "mysql", "mariadb", "monetdb"])
    parser.add_argument("--top-k", type=int, default=150)
    parser.add_argument("--accept-pool", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--max-frontier-nodes", type=int, default=512)
    parser.add_argument("--frontier-batch", type=int, default=128)
    parser.add_argument("--proposal-cap", type=int, default=20000)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--query-timeout", type=int, default=300)
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--codeql-bin", default=DEFAULT_CODEQL)
    parser.add_argument("--search-path", type=Path, default=DEFAULT_SEARCH_PATH)
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()
    if args.top_k <= 0 or args.accept_pool < args.top_k:
        raise SystemExit("accept-pool must be at least top-k")

    database = args.db_root / args.dbms
    if not database.is_dir():
        raise SystemExit(f"CodeQL database missing: {database}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    work = args.output_root / "approx_work"
    work.mkdir(parents=True, exist_ok=True)
    ensure_work_pack(work)

    universe_ql = work / "source_universe.ql"
    universe_csv = work / "source_universe.csv"
    universe_ql.write_text(universe_query(), encoding="utf-8")
    run_codeql(
        args.codeql_bin, database, universe_ql, universe_csv,
        args.search_path, args.threads, args.query_timeout,
    )
    universe, raw_universe_rows = parse_universe_csv(universe_csv)
    if len(universe) < args.top_k:
        raise RuntimeError(f"source universe={len(universe)} < K={args.top_k}")
    proposals = list(universe)
    random.Random(args.seed).shuffle(proposals)

    sampler = Sampler(args, database, work)
    accepted: dict[str, dict[str, Any]] = {}
    proposal_count = 0
    unresolved = 0
    batch_size = max(args.frontier_batch, args.top_k)
    for start in range(0, min(len(proposals), args.proposal_cap), batch_size):
        batch = proposals[start : start + batch_size]
        if not batch:
            break
        proposal_count += len(batch)
        found, not_found = sampler.accept_batch(batch)
        unresolved += not_found
        for item in found:
            accepted.setdefault(key_text(item), item)
        print(
            f"[approx_wo_m1] proposals={proposal_count} accepted={len(accepted)} "
            f"unresolved={unresolved}", flush=True,
        )
        if len(accepted) >= args.accept_pool:
            break

    if len(accepted) < args.top_k:
        raise RuntimeError(
            f"accepted={len(accepted)} < K={args.top_k}; "
            "increase proposal-cap/depth/frontier budget"
        )

    accepted_pool = list(accepted.values())
    selected = sampler.rng.sample(accepted_pool, args.top_k)
    selected.sort(key=key)
    anchors = sorted({target_line(item) for item in selected})
    if len(anchors) != args.top_k:
        raise RuntimeError(f"sampled anchors={len(anchors)} != K={args.top_k}")

    target_root = args.output_root / "targets"
    target_root.mkdir(parents=True, exist_ok=True)
    target_file = target_root / f"{args.dbms}_memory.txt"
    target_file.write_text("\n".join(anchors) + "\n", encoding="utf-8")
    full_names = load_full_m1_names(args.dbms, args.top_k)
    inventory = args.output_root / f"targets_wo_m1_{args.dbms}.csv"
    write_inventory(inventory, selected, full_names)
    rows = callchain_rows(selected)
    by_entry: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_entry.setdefault(str(row["entry"]), []).append(row)
    (target_root / "callchains.json").write_text(
        json.dumps(
            {
                "active_dbms": args.dbms,
                "by_entry": by_entry,
                args.dbms: rows,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(TOOLS))
    from prepare_wo_m1 import phi_mapping  # type: ignore

    (target_root / "phi_mapping.json").write_text(
        json.dumps(phi_mapping(selected), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (target_root / "weights.json").write_text(
        json.dumps({args.dbms: {"logic": 1.0, "memory": 1.0}}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approximation = {
        "algorithm": "demand_driven_reverse_reachability_rejection_sampling",
        "source_universe_definition": "same sourceCandidate filter as exact RQ4 pool, without calls+",
        "approximation_reason": "avoid global entry.calls+(function) materialization",
        "uniform_proposal_space": "server source-function universe",
        "bounded_reverse_search": True,
        "max_depth": args.max_depth,
        "max_frontier_nodes": args.max_frontier_nodes,
        "frontier_batch": args.frontier_batch,
        "proposal_cap": args.proposal_cap,
        "proposal_count": proposal_count,
        "accepted_pool_size": len(accepted_pool),
        "unresolved_proposals": unresolved,
        "sample_seed": args.seed,
        "query_count": sampler.query_index + 1,
        "query_stats": sampler.query_stats,
    }
    write_json(args.output_root / "APPROXIMATION.json", approximation)
    manifest = {
        "variant": "RQ4 w/o M1 (approximate SQL-reachability sampler)",
        "definition": "K functions uniformly proposed from the source universe and accepted by bounded reverse SQL reachability",
        "dbms": args.dbms,
        "top_k": args.top_k,
        "sample_seed": args.seed,
        "codeql_database": str(database),
        "drive_target_root": str(target_root),
        "target_file": str(target_file),
        "target_file_sha256": sha256_file(target_file),
        "target_inventory": str(inventory),
        "target_inventory_sha256": sha256_file(inventory),
        "source_universe_size": len(universe),
        "source_universe_raw_rows": raw_universe_rows,
        "accepted_pool_size": len(accepted_pool),
        "sampled_function_count": len(selected),
        "overlap_with_full_m1_names": sorted(
            {str(x["function"]) for x in selected} & full_names
        ),
        "approximation": approximation,
        "evaluation_rule": {
            "drive_targets_are_not_the_coverage_denominator": True,
            "coverage_denominator": "frozen Full/RQ3 target_regions.csv",
            "same_m2_and_m3": True,
            "same_source_revision_and_instrumentation_required": True,
        },
    }
    write_json(args.output_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
