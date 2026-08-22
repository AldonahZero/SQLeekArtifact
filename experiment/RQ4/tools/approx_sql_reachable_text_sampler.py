#!/usr/bin/env python3
"""Lightweight approximate SQL-reachable sampler for the RQ4 w/o-M1 ablation.

This is the MonetDB fallback when evaluating the full CodeQL call graph is too
large.  It builds a conservative, auditable name-based call graph directly
from the frozen C/C++ source snapshot, performs a bounded BFS from SQL entry
functions, and samples K function instances uniformly from that approximate
reachable set.  It deliberately does not use Stage-0 priority scores to form
the sample; those are used only for an overlap audit in the manifest.

The result is not claimed to be the exact CodeQL ``calls+`` pool.  The
manifest records the parser, depth/node/name-resolution caps, source revision,
and reachable-pool size so it can be reported as an approximate MonetDB
sensitivity experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

ROOT = Path("/root/SQLeek")
DEFAULT_SOURCE_ROOT = ROOT / "sources/monetdb"
DEFAULT_OUTPUT_ROOT = ROOT / "experiment/RQ4/configs/wo_m1/approx_monetdb_text_20260802"
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
    "sqlite3_prepare", "sqlite3_step", "sqlite3VdbeExec", "handle_connection",
    "do_command", "dispatch_command", "mysql_parse", "mysql_execute_command",
    "mysql_execute", "monetdbe_query", "monetdbe_query_internal",
    "monetdbe_query_remote", "SQLengine", "SQLparser",
)
CONTROL_NAMES = {
    "if", "for", "while", "switch", "catch", "return", "sizeof", "alignof",
    "decltype", "static_assert", "defined", "typeof", "__attribute__",
}
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
CALL_RE = re.compile(r"(?<![A-Za-z0-9_])(~?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def mask_comments_and_strings(text: str) -> str:
    """Blank comments/literals while preserving newlines and character offsets."""
    chars = list(text)
    n = len(chars)
    i = 0
    state = "code"
    while i < n:
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if ch == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if ch in {'"', "'"}:
                quote = ch
                chars[i] = " "
                i += 1
                state = quote
                continue
            i += 1
            continue
        if state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                chars[i] = " "
            i += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "code"
            else:
                if ch != "\n":
                    chars[i] = " "
                i += 1
            continue
        # Quoted string/character literal.  Keep newlines only as a guard for
        # malformed literals; normal C/C++ literals cannot span a newline.
        if ch == "\\":
            chars[i] = " "
            if i + 1 < n:
                if chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
            else:
                i += 1
            continue
        if ch == state:
            chars[i] = " "
            i += 1
            state = "code"
        else:
            if ch != "\n":
                chars[i] = " "
            i += 1
    return "".join(chars)


def matching_delimiter(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for pos in range(start, len(text)):
        ch = text[pos]
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return pos
    return None


def function_name(raw: str) -> str:
    return raw.split("::")[-1]


def source_file_allowed(source_root: Path, path: Path) -> bool:
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return False
    try:
        rel = path.relative_to(source_root)
    except ValueError:
        return False
    return not any(part.lower() in NON_SOURCE_PARTS for part in rel.parts)


def iter_source_files(source_root: Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(source_root):
        dirs[:] = sorted(
            d for d in dirs
            if d.lower() not in NON_SOURCE_PARTS and not d.startswith(".")
        )
        for name in sorted(files):
            path = Path(base) / name
            if source_file_allowed(source_root, path):
                yield path


def extract_functions(source_root: Path) -> tuple[list[dict[str, Any]], int, int]:
    """Extract function definitions with a balanced-brace lexical scan."""
    nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    file_count = 0
    for path in iter_source_files(source_root):
        file_count += 1
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        masked = mask_comments_and_strings(raw)
        # Track parenthesis depth so calls inside if/for/while conditions are
        # not mistaken for definitions whose outer block follows the call.
        paren_depth = [0] * (len(masked) + 1)
        depth = 0
        for idx, ch in enumerate(masked):
            paren_depth[idx] = depth
            if ch == "(":
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
        for match in CALL_RE.finditer(masked):
            raw_name = match.group(1)
            name = function_name(raw_name)
            if name in CONTROL_NAMES or paren_depth[match.start()] != 0:
                continue
            line_start = masked.rfind("\n", 0, match.start()) + 1
            if masked[line_start:match.start()].lstrip().startswith("#"):
                continue
            close_paren = matching_delimiter(masked, match.end() - 1, "(", ")")
            if close_paren is None:
                continue
            # A definition may have qualifiers, an initializer list, or an
            # attribute between ')' and '{'. Stop at a declaration/assignment.
            body_open: int | None = None
            scan_end = min(len(masked), close_paren + 1600)
            pos = close_paren + 1
            while pos < scan_end:
                ch = masked[pos]
                if ch == "{":
                    body_open = pos
                    break
                if ch in ";=":
                    break
                pos += 1
            if body_open is None:
                continue
            body_close = matching_delimiter(masked, body_open, "{", "}")
            if body_close is None:
                continue
            rel = path.relative_to(source_root).as_posix()
            start_line = line_number(raw, match.start(1))
            end_line = line_number(raw, body_close)
            node_key = (name, rel, start_line, end_line)
            if node_key in seen:
                continue
            seen.add(node_key)
            tokens = set(IDENT_RE.findall(masked[body_open + 1 : body_close]))
            nodes.append(
                {
                    "function": name,
                    "file": rel,
                    "start_line": start_line,
                    "end_line": end_line,
                    "call_names": tokens,
                }
            )
    nodes.sort(key=lambda item: (item["function"], item["file"], item["start_line"], item["end_line"]))
    return nodes, file_count, len(seen)


def is_sql_entry(name: str) -> bool:
    return any(pattern in name for pattern in SQL_ENTRY_PATTERNS)


def resolve_targets(
    node: dict[str, Any],
    name_index: dict[str, list[int]],
    nodes: list[dict[str, Any]],
    max_defs_per_name: int,
) -> Iterable[int]:
    for called in sorted(node["call_names"]):
        candidates = name_index.get(called)
        if not candidates:
            continue
        same_file = [idx for idx in candidates if nodes[idx]["file"] == node["file"]]
        chosen = same_file if same_file else candidates
        for idx in chosen[:max_defs_per_name]:
            yield idx


def build_reachable_pool(
    nodes: list[dict[str, Any]],
    max_depth: int,
    max_nodes: int,
    max_defs_per_name: int,
) -> tuple[list[dict[str, Any]], dict[int, int], dict[int, str], dict[str, Any]]:
    name_index: dict[str, list[int]] = defaultdict(list)
    for idx, node in enumerate(nodes):
        name_index[str(node["function"])].append(idx)
    for values in name_index.values():
        values.sort(key=lambda idx: (nodes[idx]["file"], nodes[idx]["start_line"], nodes[idx]["end_line"]))
    roots = sorted(
        idx for idx, node in enumerate(nodes) if is_sql_entry(str(node["function"]))
    )
    root_ids = set(roots)
    depths: dict[int, int] = {idx: 0 for idx in roots}
    entries: dict[int, str] = {idx: str(nodes[idx]["function"]) for idx in roots}
    queue: deque[int] = deque(roots)
    truncated = False
    while queue:
        current = queue.popleft()
        current_depth = depths[current]
        if current_depth >= max_depth:
            continue
        for target in resolve_targets(nodes[current], name_index, nodes, max_defs_per_name):
            if target in depths:
                continue
            if len(depths) >= max_nodes:
                truncated = True
                queue.clear()
                break
            depths[target] = current_depth + 1
            entries[target] = entries[current]
            queue.append(target)
    pool = [nodes[idx] for idx in depths if idx not in root_ids]
    stats = {
        "sql_entry_root_count": len(roots),
        "visited_node_count_including_entries": len(depths),
        "reachable_pool_size_excluding_entries": len(pool),
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "max_defs_per_name": max_defs_per_name,
        "truncated_by_max_nodes": truncated,
        "depth_histogram": {
            str(level): sum(1 for value in depths.values() if value == level)
            for level in range(max_depth + 1)
        },
    }
    return pool, depths, entries, stats


def target_line(item: dict[str, Any]) -> str:
    return f"{item['file']}:{int(item['start_line'])}-{int(item['end_line'])}"


def callchain_rows(selected: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(selected, key=lambda row: (row["function"], row["file"], row["start_line"])):
        rows.append(
            {
                "entry": str(item["entry"]),
                "target": str(item["function"]),
                "danger_fn": str(item["function"]),
                "depth": int(item["depth"]),
                "functions": [str(item["entry"]), str(item["function"])],
                "path": [str(item["entry"]), str(item["function"])],
                "source": "rq4_wo_m1_text_call_graph_approx",
                "file": str(item["file"]),
                "start_line": int(item["start_line"]),
                "end_line": int(item["end_line"]),
            }
        )
    return rows


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


def source_revision(source_root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", default="monetdb", choices=["monetdb"])
    parser.add_argument("--top-k", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--max-depth", type=int, default=15)
    parser.add_argument("--max-nodes", type=int, default=100000)
    parser.add_argument("--max-defs-per-name", type=int, default=8)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.top_k <= 0 or args.max_depth <= 0 or args.max_nodes < args.top_k:
        raise SystemExit("invalid top-k/depth/max-nodes")
    if not args.source_root.is_dir():
        raise SystemExit(f"missing source root: {args.source_root}")
    if args.output_root.exists() and any(args.output_root.iterdir()) and not args.overwrite:
        raise SystemExit(f"output exists and is non-empty: {args.output_root}")
    args.output_root.mkdir(parents=True, exist_ok=True)

    nodes, file_count, parsed_definition_count = extract_functions(args.source_root)
    if not nodes:
        raise RuntimeError("no C/C++ function definitions extracted")
    pool, depths, entries, graph_stats = build_reachable_pool(
        nodes, args.max_depth, args.max_nodes, args.max_defs_per_name
    )
    if len(pool) < args.top_k:
        raise RuntimeError(
            f"approximate reachable pool={len(pool)} < K={args.top_k}; "
            "increase depth/name-resolution/node caps"
        )

    pool_object_ids = {id(item) for item in pool}
    pool_indices = [idx for idx, node in enumerate(nodes) if id(node) in pool_object_ids]
    selected_indices = random.Random(args.seed).sample(pool_indices, args.top_k)
    selected = []
    for idx in selected_indices:
        item = dict(nodes[idx])
        item.pop("call_names", None)
        item["depth"] = depths[idx]
        item["entry"] = entries[idx]
        selected.append(item)
    selected.sort(key=lambda row: (row["function"], row["file"], row["start_line"], row["end_line"]))
    anchors = sorted({target_line(item) for item in selected})
    if len(anchors) != args.top_k:
        raise RuntimeError(f"sampled anchors={len(anchors)} != K={args.top_k}")

    target_root = args.output_root / "targets"
    target_root.mkdir(parents=True, exist_ok=True)
    target_file = target_root / f"{args.dbms}_memory.txt"
    target_file.write_text("\n".join(anchors) + "\n", encoding="utf-8")
    full_names = load_full_m1_names(args.dbms, args.top_k)
    inventory = args.output_root / f"targets_wo_m1_{args.dbms}.csv"
    import csv
    with inventory.open("w", newline="", encoding="utf-8") as fp:
        fields = [
            "rank", "function", "entry", "file", "start_line", "end_line",
            "depth", "target_line", "is_full_m1_name",
        ]
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for rank, item in enumerate(selected, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "function": item["function"],
                    "entry": item["entry"],
                    "file": item["file"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                    "depth": item["depth"],
                    "target_line": target_line(item),
                    "is_full_m1_name": item["function"] in full_names,
                }
            )

    rows = callchain_rows(selected)
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_entry[str(row["entry"])].append(row)
    callchains = {
        "active_dbms": args.dbms,
        "by_entry": dict(sorted(by_entry.items())),
        args.dbms: rows,
    }
    (target_root / "callchains.json").write_text(
        json.dumps(callchains, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.path.insert(0, str(ROOT / "experiment/RQ4/tools"))
    from prepare_wo_m1 import phi_mapping  # type: ignore
    (target_root / "phi_mapping.json").write_text(
        json.dumps(phi_mapping(selected), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (target_root / "weights.json").write_text(
        json.dumps({args.dbms: {"logic": 1.0, "memory": 1.0}}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    approximation = {
        "algorithm": "bounded_source_text_call_graph_bfs_uniform_sampling",
        "pool_definition": "functions reachable from SQL-entry-name definitions in a lexical C/C++ call-name graph",
        "not_exact_codeql_calls_plus": True,
        "source_root": str(args.source_root),
        "source_revision": source_revision(args.source_root),
        "source_file_count": file_count,
        "parsed_function_definition_count": parsed_definition_count,
        "graph": graph_stats,
        "sampling": {"seed": args.seed, "top_k": args.top_k, "uniform_over_approx_pool": True},
        "name_resolution": {
            "same_file_definitions_preferred": True,
            "max_definitions_per_called_name": args.max_defs_per_name,
            "comments_and_literals_removed_before_tokenization": True,
        },
    }
    write_json(args.output_root / "APPROXIMATION.json", approximation)
    manifest = {
        "variant": "RQ4 w/o M1 (approximate MonetDB text-call-graph sampler)",
        "definition": "uniform sample of K functions from a bounded lexical approximation of the SQL-reachable pool",
        "dbms": args.dbms,
        "top_k": args.top_k,
        "sample_seed": args.seed,
        "source_root": str(args.source_root),
        "drive_target_root": str(target_root),
        "target_file": str(target_file),
        "target_file_sha256": sha256_file(target_file),
        "target_inventory": str(inventory),
        "target_inventory_sha256": sha256_file(inventory),
        "approximation": approximation,
        "overlap_with_full_m1_names": sorted(
            {str(item["function"]) for item in selected} & full_names
        ),
        "evaluation_rule": {
            "drive_targets_are_not_the_coverage_denominator": True,
            "coverage_denominator": "frozen Full/RQ3 target_regions.csv",
            "same_m2_and_m3": True,
            "same_source_revision_and_instrumentation_required": True,
            "paper_label": "approximate w/o-M1 sensitivity result; do not call exact uniform SQL-reachable sampling",
        },
    }
    write_json(args.output_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
