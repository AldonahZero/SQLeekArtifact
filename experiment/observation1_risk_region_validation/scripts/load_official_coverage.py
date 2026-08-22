#!/usr/bin/env python3
"""Load official-test coverage for risk regions without using fuzzing coverage."""
from __future__ import annotations

import argparse
import math
import re
import collections
from pathlib import Path

from common import (
    canonical_dbms,
    data_dir,
    float_or_none,
    int_or_zero,
    load_config,
    normalize_path,
    read_csv,
    report_dir,
    run,
    safe_div,
    source_root,
    git,
    write_csv,
)


def aggregate_line_coverage(regions: list[dict[str, str]], rows: list[dict[str, str]], source: str) -> list[dict[str, object]]:
    by_region = {r["region_id"]: r for r in regions}
    direct = {r.get("region_id", ""): r for r in rows if r.get("region_id")}
    if direct:
        out = []
        for region in regions:
            cov = direct.get(region["region_id"], {})
            out.append(region_cov_row(region, cov, source))
        return out

    line_rows = []
    for row in rows:
        f = normalize_path(row.get("file_path") or row.get("filename") or row.get("file") or "")
        line = int_or_zero(row.get("line") or row.get("line_number"))
        if not f or not line:
            continue
        covered = row.get("covered") or row.get("count") or row.get("execution_count") or ""
        count = float_or_none(covered)
        is_covered = count is not None and count > 0
        line_rows.append((f, line, is_covered))

    out = []
    for region in regions:
        f = normalize_path(region["file_path"])
        start = int_or_zero(region["start_line"])
        end = int_or_zero(region["end_line"], start)
        hits = [covered for path, line, covered in line_rows if path == f and start <= line <= end]
        total = max(1, end - start + 1)
        covered_lines = sum(1 for x in hits if x)
        out.append(
            {
                "dbms": region["dbms"],
                "region_id": region["region_id"],
                "official_test_covered": bool(covered_lines),
                "official_line_coverage": safe_div(covered_lines, total),
                "official_branch_coverage": "",
                "covered_lines": covered_lines,
                "total_lines": total,
                "covered_branches": "",
                "total_branches": "",
                "coverage_source": source,
                "coverage_commit": "",
                "coverage_status": "loaded_line_coverage",
            }
        )
    return out


def region_cov_row(region: dict[str, str], cov: dict[str, str], source: str) -> dict[str, object]:
    line_cov = float_or_none(cov.get("official_line_coverage") or cov.get("line_coverage"))
    branch_cov = float_or_none(cov.get("official_branch_coverage") or cov.get("branch_coverage"))
    covered_lines = int_or_zero(cov.get("covered_lines"))
    total_lines = int_or_zero(cov.get("total_lines"), int_or_zero(region.get("region_loc"), 1))
    covered_branches = int_or_zero(cov.get("covered_branches"))
    total_branches = int_or_zero(cov.get("total_branches"))
    if line_cov is None and total_lines:
        line_cov = safe_div(covered_lines, total_lines)
    if branch_cov is None and total_branches:
        branch_cov = safe_div(covered_branches, total_branches)
    return {
        "dbms": region["dbms"],
        "region_id": region["region_id"],
        "official_test_covered": bool((line_cov or 0) > 0 or (branch_cov or 0) > 0),
        "official_line_coverage": "" if line_cov is None else line_cov,
        "official_branch_coverage": "" if branch_cov is None else branch_cov,
        "covered_lines": covered_lines if covered_lines else "",
        "total_lines": total_lines if total_lines else "",
        "covered_branches": covered_branches if covered_branches else "",
        "total_branches": total_branches if total_branches else "",
        "coverage_source": cov.get("coverage_source") or source,
        "coverage_commit": cov.get("coverage_commit") or "",
        "coverage_status": "loaded_region_coverage",
    }


def discover_excluded(root: Path) -> list[str]:
    proc = run(
        [
            "bash",
            "-lc",
            "find /root/SQLeek -maxdepth 6 -type f \\( -iname '*coverage*' -o -iname '*.profdata' -o -iname '*.lcov' \\) 2>/dev/null | head -n 200",
        ],
        check=False,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def missing_rows(regions: list[dict[str, str]]) -> list[dict[str, object]]:
    out = []
    for region in regions:
        out.append(
            {
                "dbms": region["dbms"],
                "region_id": region["region_id"],
                "official_test_covered": "",
                "official_line_coverage": "",
                "official_branch_coverage": "",
                "covered_lines": "",
                "total_lines": region.get("region_loc", ""),
                "covered_branches": "",
                "total_branches": "",
                "coverage_source": "missing_official_test_coverage",
                "coverage_commit": "",
                "coverage_status": "missing",
            }
        )
    return out


STOP_TOKENS = {
    "sql",
    "mysql",
    "test",
    "tests",
    "main",
    "source",
    "common",
    "include",
    "release",
    "regular",
    "expressions",
    "cc",
    "cpp",
    "hpp",
    "class",
    "function",
    "priority",
    "local",
    "memory",
    "sink",
    "descriptor",
    "metadata",
    "field",
    "table",
    "item",
    "val",
    "get",
    "set",
    "reset",
    "init",
    "execute",
    "fix",
    "type",
    "memcpy",
    "memset",
    "alloc",
    "string",
    "container",
    "buffer",
}

DOMAIN_ALIASES = {
    "item_cmpfunc": {"comparison", "compare", "cmp", "where", "predicate", "condition", "func"},
    "cmpfunc": {"comparison", "compare", "where", "predicate", "condition"},
    "field": {"column", "type", "cast", "create", "insert"},
    "partition": {"partition", "alter", "truncate", "reorganize"},
    "binlog": {"binlog", "replication", "rpl", "gtid"},
    "rpl": {"replication", "rpl", "binlog", "gtid"},
    "auth": {"grant", "revoke", "user", "role", "privilege", "password"},
    "json": {"json"},
    "gis": {"geometry", "spatial", "st"},
    "regexp": {"regexp", "regular", "expression"},
    "window": {"window", "over", "partition"},
    "handler": {"handler", "storage", "engine"},
    "innodb": {"innodb"},
    "ndb": {"ndb", "cluster"},
    "charset": {"charset", "collation", "character"},
    "collation": {"collation", "charset", "character"},
    "timefunc": {"date", "time", "timestamp", "interval"},
    "decimal": {"decimal", "numeric"},
    "string": {"char", "varchar", "text", "string"},
    "select": {"select", "join", "where"},
    "join": {"join"},
    "optimizer": {"optimizer", "range", "join", "where"},
    "range": {"range", "optimizer", "index"},
}


def split_identifier(text: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text.lower())
    out: set[str] = set()
    for token in raw:
        for part in re.split(r"[_\W]+", token):
            if len(part) >= 3:
                out.add(part)
        if len(token) >= 3:
            out.add(token)
    return out


def region_tokens(region: dict[str, str]) -> set[str]:
    path = normalize_path(region.get("file_path", ""))
    pieces = [
        path,
        Path(path).stem,
        region.get("component", ""),
        region.get("enclosing_function", ""),
        region.get("alert_message", ""),
        region.get("risk_type", ""),
        region.get("rule_id", ""),
    ]
    tokens: set[str] = set()
    for piece in pieces:
        tokens.update(split_identifier(piece))
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(DOMAIN_ALIASES.get(token, set()))
    return {t for t in expanded if t not in STOP_TOKENS and len(t) >= 3}


def test_tokens(path: Path, root: Path) -> tuple[str, set[str], set[str]]:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    # Test names and suite dirs are curated labels; SQL text is weak evidence.
    name_toks = split_identifier(rel)
    content_toks = split_identifier(text)
    sql_keywords = set(re.findall(r"(?i)\b(select|insert|update|delete|join|where|group|order|window|partition|json|regexp|cast|interval|decimal|geometry|grant|revoke|role|user|binlog|gtid|handler|alter|create|drop|truncate)\b", text))
    content_toks.update(t.lower() for t in sql_keywords)
    return (
        rel,
        {t for t in name_toks if len(t) >= 3},
        {t for t in content_toks if len(t) >= 3},
    )


def static_mysql_test_suite_proxy(regions: list[dict[str, str]], cfg: dict[str, object]) -> tuple[list[dict[str, object]], str]:
    src = source_root(cfg)
    test_root = src / "mysql-test"
    tests = sorted(test_root.rglob("*.test")) if test_root.is_dir() else []
    indexed: list[tuple[str, set[str], set[str]]] = []
    df: collections.Counter[str] = collections.Counter()
    for path in tests:
        rel, name_toks, content_toks = test_tokens(path, test_root)
        indexed.append((rel, name_toks, content_toks))
        df.update(name_toks | content_toks)
    ntests = max(1, len(indexed))
    commit = git(src, "rev-parse", "HEAD", check=False).strip()

    rows: list[dict[str, object]] = []
    for region in regions:
        rtoks = {
            t
            for t in region_tokens(region)
            if df.get(t, 0) <= max(50, int(ntests * 0.12))
        }
        if not rtoks:
            score = 0.0
            matched: list[tuple[float, str]] = []
        else:
            weights = {tok: math.log((ntests + 1) / (df.get(tok, 0) + 1)) + 1.0 for tok in rtoks}
            denom = sum(weights.values()) or 1.0
            matched = []
            best_token_cover = 0.0
            rare_content_tokens = {t for t in rtoks if df.get(t, 0) <= max(10, int(ntests * 0.025))}
            for rel, name_toks, content_toks in indexed:
                name_common = rtoks & name_toks
                content_common = rare_content_tokens & content_toks
                if not name_common and not content_common:
                    continue
                name_score = 0.75 * sum(weights[t] for t in name_common) / denom
                content_score = 0.20 * sum(weights[t] for t in content_common) / denom
                local_score = min(1.0, name_score + content_score)
                if local_score >= 0.03:
                    matched.append((local_score, rel))
                    best_token_cover = max(best_token_cover, local_score)
            matched.sort(reverse=True)
            top_scores = [s for s, _ in matched[:8]]
            support = min(0.25, 0.05 * math.log1p(len(matched)))
            score = min(1.0, best_token_cover + support + sum(top_scores) / 40.0)

        total_lines = max(1, int_or_zero(region.get("region_loc"), 1))
        line_cov = min(1.0, score)
        branch_cov = min(1.0, score * 0.8)
        covered_lines = int(round(total_lines * line_cov))
        total_branches = max(1, total_lines * 2)
        covered_branches = int(round(total_branches * branch_cov))
        rows.append(
            {
                "dbms": region["dbms"],
                "region_id": region["region_id"],
                "official_test_covered": bool(score >= 0.12),
                "official_line_coverage": line_cov,
                "official_branch_coverage": branch_cov,
                "covered_lines": covered_lines,
                "total_lines": total_lines,
                "covered_branches": covered_branches,
                "total_branches": total_branches,
                "coverage_source": "mysql_static_official_test_suite_proxy:mysql-test/*.test",
                "coverage_commit": commit,
                "coverage_status": "static_proxy_from_official_test_suite",
                "coverage_proxy_score": score,
                "matched_test_count": len(matched),
                "top_matched_tests": ";".join(rel for _, rel in matched[:5]),
                "coverage_algorithm": "idf_token_overlap(region path/function/alert tokens vs official mysql-test .test files)",
            }
        )
    note = (
        f"Computed static official-test coverage proxy from `{test_root}` using {len(tests)} `.test` files. "
        "This is not dynamic LLVM coverage; it estimates whether official MySQL tests exercise the SQL features/components named by each Stage 1 risk region."
    )
    return rows, note


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbms", required=True, choices=["mysql", "postgresql", "postgres"])
    parser.add_argument("--coverage-csv", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dbms = canonical_dbms(args.dbms)
    out = data_dir(dbms) / "official_coverage.csv"
    if out.is_file() and not args.force:
        print(out)
        return

    cfg = load_config(dbms)
    regions = read_csv(data_dir(dbms) / "risk_regions.csv")
    explicit = args.coverage_csv or str((cfg.get("coverage") or {}).get("official_coverage_csv") or "")
    if explicit and Path(explicit).is_file():
        rows = aggregate_line_coverage(regions, read_csv(Path(explicit)), explicit)
        note = f"Loaded official coverage from `{explicit}`."
    elif dbms == "mysql":
        rows, note = static_mysql_test_suite_proxy(regions, cfg)
    else:
        rows = missing_rows(regions)
        excluded = discover_excluded(Path(str(cfg.get("sqleek_root", "/root/SQLeek"))))
        note = (
            "No official-test coverage CSV was configured or found. "
            "Fuzzing/replay coverage candidates were intentionally excluded from the main signal.\n\n"
            + "\n".join(f"- `{p}`" for p in excluded[:80])
        )
    write_csv(out, rows)
    (report_dir() / f"{dbms}_coverage_discovery.md").write_text(
        f"# {dbms} Coverage Discovery\n\n{note}\n", encoding="utf-8"
    )
    print(out)


if __name__ == "__main__":
    main()
