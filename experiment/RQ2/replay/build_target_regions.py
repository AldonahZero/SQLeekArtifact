#!/usr/bin/env python3
"""Build RQ2 target_regions.csv from Stage-1 target anchor files."""
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

DEFAULT_TARGETS = Path("/root/SQLeek/sqleek_pipeline/stage1_static/output/targets")
DEFAULT_SOURCES = Path("/root/SQLeek/sources")
RULES = ("stale", "memory", "logic")
CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "foreach"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dbms", default="mysql,postgres,sqlite")
    p.add_argument("--targets-dir", type=Path, default=DEFAULT_TARGETS)
    p.add_argument("--sources-root", type=Path, default=DEFAULT_SOURCES)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--window", type=int, default=5)
    return p.parse_args()


def read_anchors(targets_dir: Path, dbms: str) -> list[dict[str, object]]:
    anchors: list[dict[str, object]] = []
    for rule in RULES:
        path = targets_dir / f"{dbms}_{rule}.txt"
        if not path.exists():
            continue
        for raw in path.read_text(errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            m = re.match(r"(.+):(\d+)$", raw)
            if m:
                anchors.append(
                    {
                        "dbms": dbms,
                        "rule_family": rule,
                        "file_hint": m.group(1),
                        "anchor_line": int(m.group(2)),
                    }
                )
    return anchors


def build_file_index(root: Path) -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = defaultdict(list)
    if not root.exists():
        return idx
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".c", ".cc", ".cpp", ".h", ".hpp", ".hh"}:
            idx[path.name].append(path)
    for paths in idx.values():
        paths.sort(key=lambda p: (len(str(p)), str(p)))
    return idx


def dbms_source_root(sources_root: Path, dbms: str) -> Path:
    return sources_root / {"postgresql": "postgres"}.get(dbms, dbms)


def resolve_source(file_index: dict[str, list[Path]], file_hint: str) -> Path | None:
    return (file_index.get(Path(file_hint).name) or [None])[0]


def function_name_from_signature(sig: str) -> str | None:
    sig = re.sub(r"/\*.*?\*/", " ", sig)
    sig = re.sub(r"//.*", " ", sig)
    sig = " ".join(sig.split())
    if "(" not in sig or ")" not in sig:
        return None
    before = sig.split("(", 1)[0].strip()
    if not before:
        return None
    name = before.split()[-1].strip("*&~").split("::")[-1]
    if name in CONTROL_WORDS or not re.match(r"[A-Za-z_~][A-Za-z0-9_~]*$", name):
        return None
    return name


def find_function_span(path: Path, line_no: int) -> tuple[int, int, str, str] | None:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception:
        return None
    if line_no < 1 or line_no > len(lines):
        return None
    start_idx = None
    sig_lines: list[str] = []
    for i in range(line_no - 1, max(0, line_no - 220) - 1, -1):
        sig_lines.insert(0, lines[i].strip())
        if "{" not in lines[i]:
            continue
        sig = " ".join(sig_lines[-8:])
        if function_name_from_signature(sig):
            start_idx = i
            break
        sig_lines = []
    if start_idx is None:
        return None
    depth = 0
    seen_open = False
    for j in range(start_idx, len(lines)):
        if "{" in lines[j]:
            seen_open = True
        depth += lines[j].count("{") - lines[j].count("}")
        if seen_open and depth <= 0:
            sig = " ".join(x.strip() for x in lines[max(0, start_idx - 5) : start_idx + 1])
            return start_idx + 1, j + 1, function_name_from_signature(sig) or "", "function"
    return None


def infer_component(rel: str, function: str) -> str:
    s = f"{rel}/{function}".lower()
    checks = [
        ("parser", ("parse", "parser", "gram", "scan", "lexer", "token", "sql_yacc")),
        ("optimizer", ("opt", "planner", "join", "cost", "range", "where", "selectivity")),
        ("executor", ("exec", "vdbe", "evaluate", "eval", "interp", "query")),
        ("type_system", ("type", "field", "item", "datum", "json", "date", "time", "decimal", "collat")),
        ("catalog_metadata", ("catalog", "schema", "dict", "metadata", "table", "column", "namespace", "pg_")),
        ("storage", ("store", "storage", "innodb", "btree", "pager", "wal", "heap", "buf", "record")),
        ("cursor_prepared", ("cursor", "prepare", "prepared", "stmt", "portal")),
    ]
    for comp, keys in checks:
        if any(k in s for k in keys):
            return comp
    return "other"


def main() -> None:
    args = parse_args()
    rows: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for dbms in [x.strip().lower() for x in args.dbms.split(",") if x.strip()]:
        source_root = dbms_source_root(args.sources_root, dbms)
        file_index = build_file_index(source_root)
        for anchor in read_anchors(args.targets_dir, dbms):
            src = resolve_source(file_index, str(anchor["file_hint"]))
            anchor_line = int(anchor["anchor_line"])
            if src:
                rel = str(src.relative_to(source_root)) if src.is_relative_to(source_root) else str(src)
                span = find_function_span(src, anchor_line)
                if span and span[0] <= anchor_line <= span[1]:
                    start, end, func, span_source = span
                else:
                    line_count = len(src.read_text(errors="replace").splitlines())
                    start = max(1, anchor_line - args.window)
                    end = min(line_count, anchor_line + args.window)
                    func = ""
                    span_source = "window"
            else:
                rel = str(anchor["file_hint"])
                start = max(1, anchor_line - args.window)
                end = anchor_line + args.window
                func = ""
                span_source = "unresolved_window"
            key = (dbms, rel, start, end, anchor["rule_family"], func)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "region_id": f"{dbms}_target_{len(rows) + 1:06d}",
                    "dbms": dbms,
                    "rule_family": anchor["rule_family"],
                    "component": infer_component(rel, func),
                    "file": Path(rel).name,
                    "source_path": rel,
                    "anchor_file": anchor["file_hint"],
                    "anchor_line": anchor_line,
                    "start_line": start,
                    "end_line": end,
                    "function": func,
                    "span_source": span_source,
                }
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "region_id",
        "dbms",
        "rule_family",
        "component",
        "file",
        "source_path",
        "anchor_file",
        "anchor_line",
        "start_line",
        "end_line",
        "function",
        "span_source",
    ]
    with args.out.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.out} rows={len(rows)}")


if __name__ == "__main__":
    main()
