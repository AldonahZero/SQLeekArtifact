#!/usr/bin/env python3
"""Prepare RQ4 w/o-M1 targets from a SQL-reachable CodeQL function pool."""
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
DBMS_LIST = ("postgres", "mysql", "mariadb", "monetdb")
DEFAULT_QUERY = ROOT / "experiment/RQ4/tools/sql_reachable_function_pool.ql"
DEFAULT_DB_ROOT = ROOT / "sqleek_pipeline/stage1_static/output/codeql_db"
DEFAULT_RAW_ROOT = ROOT / "experiment/RQ4/configs/wo_m1/pool_raw"
DEFAULT_OUTPUT_ROOT = ROOT / "experiment/RQ4/configs/wo_m1/seed_20260731"
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx"}
NON_SOURCE_PARTS = {
    "build",
    "builds",
    "generated",
    "third_party",
    "vendor",
    # SQL entry-point call graphs can conservatively resolve same-named
    # functions in client utilities and bundled third-party/test trees. The
    # ablation pool is intended to cover DBMS server implementation functions.
    "client",
    "clients",
    "extra",
    "test",
    "tests",
    "unittest",
    "examples",
    "example",
    "bench",
    "benchmark",
    "doc",
    "docs",
    "scripts",
    "support-files",
    "packaging",
    "debian",
    "win",
    "windows",
}
MESSAGE_RE = re.compile(
    r"entry=(?P<entry>[^;\r\n]+);"
    r"function=(?P<function>[^;\r\n]+);"
    r"file=(?P<file>[^;\r\n]+);"
    r"start=(?P<start>\d+);"
    r"end=(?P<end>\d+);"
    r"depth=(?P<depth>\d+)"
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


def candidate_key(item: dict[str, Any]) -> tuple[str, str, int, int]:
    return (
        str(item["function"]),
        str(item["file"]),
        int(item["start_line"]),
        int(item["end_line"]),
    )


def path_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    return (
        int(item["depth"]),
        str(item["entry"]),
        int(item["start_line"]),
        int(item["end_line"]),
    )


def valid_candidate(item: dict[str, Any]) -> bool:
    path = Path(str(item["file"]))
    start = int(item["start_line"])
    end = int(item["end_line"])
    if not item["function"] or not item["entry"] or start <= 0 or end < start:
        return False
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return False
    return not any(part.lower() in NON_SOURCE_PARTS for part in path.parts)


def parse_pool_csv(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse CodeQL problem CSV, including coalesced newline-separated messages."""
    # CodeQL may coalesce many problem messages into one CSV field. The default
    # csv module limit (128 KiB) is too small for large DBMS databases.
    csv.field_size_limit(sys.maxsize)
    candidates: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    raw_rows = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        for row in csv.reader(fp):
            raw_rows += 1
            for field in row:
                for message in field.splitlines():
                    match = MESSAGE_RE.search(message.strip())
                    if not match:
                        continue
                    item: dict[str, Any] = {
                        "entry": match.group("entry").strip(),
                        "function": match.group("function").strip(),
                        "file": match.group("file").strip(),
                        "start_line": int(match.group("start")),
                        "end_line": int(match.group("end")),
                        "depth": int(match.group("depth")),
                    }
                    if not valid_candidate(item):
                        continue
                    key = (
                        item["function"],
                        item["file"],
                        item["start_line"],
                        item["end_line"],
                    )
                    old = candidates.get(key)
                    if old is None or path_key(item) < path_key(old):
                        candidates[key] = item
    return sorted(candidates.values(), key=candidate_key), raw_rows


def load_full_m1_functions(dbms: str, top_k: int) -> list[str]:
    sys.path.insert(0, str(ROOT))
    from sqleek_pipeline.stage1_static.tools.gen_priority_qll import (  # type: ignore
        load_selected,
        resolve_stage0,
    )

    stage0 = resolve_stage0(dbms, None)
    if stage0 is None:
        raise RuntimeError(f"{dbms}: Stage 0 priority score JSON not found")
    names = load_selected(stage0, top_k, dbms)
    if len(names) != top_k:
        raise RuntimeError(f"{dbms}: Full M1 K={len(names)} != requested K={top_k}")
    return names


def run_codeql(
    dbms: str,
    database: Path,
    query: Path,
    raw_csv: Path,
    codeql_bin: str,
    search_path: Path,
    threads: int,
) -> None:
    raw_csv.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = f"{Path(codeql_bin).parent}:{env.get('PATH', '')}"
    env["CODEQL_ALLOW_INSTALLATION_ANYWHERE"] = "true"
    command = [
        codeql_bin,
        "database",
        "analyze",
        f"--search-path={search_path}",
        "--format=csv",
        f"--output={raw_csv}",
        "--rerun",
        f"--threads={threads}",
        str(database),
        str(query),
    ]
    print(f"[wo_m1] CodeQL {dbms}: {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, env=env)


def target_line(item: dict[str, Any]) -> str:
    # Keep CodeQL's relative source path and end line. Stage 1 historically
    # reduced this to basename:line, but DBMS trees contain many same-named
    # files and CodeQL can report same-name functions sharing a start line.
    # The scheduler treats this as an opaque text token, so the richer anchor
    # keeps one drive anchor per sampled function instance.
    source_path = Path(str(item["file"])).as_posix()
    return f"{source_path}:{int(item['start_line'])}-{int(item['end_line'])}"


def callchain_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in sorted(rows, key=candidate_key):
        entry = str(row["entry"])
        function = str(row["function"])
        out.append(
            {
                "entry": entry,
                "target": function,
                "danger_fn": function,
                "depth": int(row["depth"]),
                "functions": [entry, function],
                "path": [entry, function],
                "source": "rq4_wo_m1_sql_reachable_function_pool",
                "file": str(row["file"]),
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
            }
        )
    return out


def write_target_inventory(
    path: Path,
    rows: Iterable[dict[str, Any]],
    full_names: Iterable[str],
) -> None:
    """Write an auditable sampled-target table separate from drive anchors."""
    full_name_set = set(full_names)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "function",
        "entry",
        "file",
        "start_line",
        "end_line",
        "depth",
        "target_line",
        "is_full_m1_name",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(sorted(rows, key=candidate_key), start=1):
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
                    "is_full_m1_name": str(row["function"]) in full_name_set,
                }
            )


def entry_map(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entry = str(row["entry"])
        out.setdefault(entry, []).append(
            {
                "danger_fn": str(row["function"]),
                "depth": int(row["depth"]),
                "path": [entry, str(row["function"])],
            }
        )
    for values in out.values():
        values.sort(key=lambda item: (int(item["depth"]), str(item["danger_fn"])))
    return dict(sorted(out.items()))


def phi_mapping(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    try:
        sys.path.insert(0, str(ROOT))
        from sqleek_pipeline.stage1_static.gen_phi_mapping import GROUND_TRUTH  # type: ignore
    except Exception:
        GROUND_TRUTH = {}
    functions = sorted({str(row["function"]) for row in rows})
    return {
        "phi_mapping": {
            fn: list(GROUND_TRUTH[fn]) if fn in GROUND_TRUTH else ["UNKNOWN"]
            for fn in functions
        },
        "validation_vs_ground_truth": {},
        "generation_method": "deterministic ground-truth for known functions; UNKNOWN otherwise",
        "callchains_source": "rq4_wo_m1_sql_reachable_function_pool",
    }


def prepare_dbms(dbms: str, args: argparse.Namespace, target_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    database = args.db_root / dbms
    if not database.is_dir():
        raise RuntimeError(f"{dbms}: CodeQL database missing: {database}")

    raw_csv = args.raw_root / f"{dbms}.csv"
    if args.refresh_pool or not raw_csv.is_file():
        run_codeql(
            dbms,
            database,
            args.query,
            raw_csv,
            args.codeql_bin,
            args.search_path,
            args.threads,
        )

    pool, raw_rows = parse_pool_csv(raw_csv)
    full_names = load_full_m1_functions(dbms, args.top_k)
    if len(pool) < args.top_k:
        raise RuntimeError(f"{dbms}: pool={len(pool)} < K={args.top_k}")

    selected = random.Random(args.seed).sample(pool, args.top_k)
    selected.sort(key=candidate_key)
    anchors = sorted({target_line(row) for row in selected})
    if len(anchors) != args.top_k:
        raise RuntimeError(f"{dbms}: sampled anchors={len(anchors)} != K={args.top_k}")

    target_file = target_root / f"{dbms}_memory.txt"
    target_file.write_text("\n".join(anchors) + "\n", encoding="utf-8")
    inventory_file = args.output_root / f"targets_wo_m1_{dbms}.csv"
    write_target_inventory(inventory_file, selected, full_names)
    rows = callchain_rows(selected)
    summary = {
        "dbms": dbms,
        "codeql_database": str(database),
        "raw_pool_csv": str(raw_csv),
        "raw_csv_sha256": sha256_file(raw_csv),
        "raw_csv_rows": raw_rows,
        "filtered_pool_size": len(pool),
        "full_m1_k": args.top_k,
        "full_m1_function_names": full_names,
        "sample_seed": args.seed,
        "sampled_function_count": len(selected),
        "sampled_function_names": sorted({str(row["function"]) for row in selected}),
        "sampled_functions": selected,
        "overlap_with_full_m1_names": sorted(
            {str(row["function"]) for row in selected} & set(full_names)
        ),
        "target_file": str(target_file),
        "target_file_sha256": sha256_file(target_file),
        "target_inventory": str(inventory_file),
        "target_inventory_sha256": sha256_file(inventory_file),
        "callchain_count": len(rows),
    }
    return summary, selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", action="append", choices=DBMS_LIST)
    parser.add_argument("--top-k", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--query", type=Path, default=DEFAULT_QUERY)
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--codeql-bin", default="/root/codeql/codeql")
    parser.add_argument("--search-path", type=Path, default=Path("/root/codeql/qlpacks"))
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--refresh-pool", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dbms_list = tuple(args.dbms or DBMS_LIST)
    if args.top_k <= 0 or args.threads <= 0:
        raise SystemExit("--top-k and --threads must be positive")
    if not args.query.is_file():
        raise SystemExit(f"missing query: {args.query}")
    args.raw_root.mkdir(parents=True, exist_ok=True)
    if args.output_root.exists() and any(args.output_root.iterdir()) and not args.overwrite:
        raise SystemExit(f"output exists and is non-empty: {args.output_root}")

    target_root = args.output_root / "targets"
    target_root.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, Any] = {}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    for dbms in dbms_list:
        summary, selected = prepare_dbms(dbms, args, target_root)
        summaries[dbms] = summary
        all_rows[dbms] = selected

    active = dbms_list[0]
    payload: dict[str, Any] = {
        "source": "rq4_wo_m1_sql_reachable_function_pool",
        "active_dbms": active,
        "chains": callchain_rows(all_rows[active]),
        "by_entry": entry_map(all_rows[active]),
    }
    for dbms in dbms_list:
        payload[dbms] = callchain_rows(all_rows[dbms])
    (target_root / "callchains.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    combined_rows = [row for rows in all_rows.values() for row in rows]
    (target_root / "phi_mapping.json").write_text(
        json.dumps(phi_mapping(combined_rows), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (target_root / "weights.json").write_text(
        json.dumps({dbms: {"logic": 1.0, "memory": 1.0} for dbms in dbms_list}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "variant": "RQ4 w/o M1",
        "definition": "uniform sample of K source functions reachable from SQL entry points",
        "sample_seed": args.seed,
        "top_k": args.top_k,
        "max_call_depth": 15,
        "query": str(args.query),
        "query_sha256": sha256_file(args.query),
        "drive_target_root": str(target_root),
        "evaluation_rule": {
            "drive_targets_are_not_the_coverage_denominator": True,
            "coverage_denominator": "frozen Full/RQ3 target_regions.csv",
            "same_source_revision_and_instrumentation_required": True,
        },
        "dbms": summaries,
        "callchains_sha256": sha256_file(target_root / "callchains.json"),
        "phi_mapping_sha256": sha256_file(target_root / "phi_mapping.json"),
        "weights_sha256": sha256_file(target_root / "weights.json"),
    }
    write_json(args.output_root / "MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
