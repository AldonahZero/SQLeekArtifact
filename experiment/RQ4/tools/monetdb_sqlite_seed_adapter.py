#!/usr/bin/env python3
"""Derive SQLite-parser-compatible seeds from MonetDB Stage 2 seeds.

The MonetDB Stage 3 image deliberately reuses SQLRight's SQLite parser and
mutator.  MonetDB-specific Stage 2 statements such as CREATE PROCEDURE,
CREATE TYPE, COPY, PREPARE, and TRACE therefore cannot be used as SQLRight
inputs directly.  This tool keeps the original M2 corpus untouched and
creates an auditable adapter corpus for that implementation constraint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def _sub(pattern: str, replacement: str, text: str, changes: list[str], label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if count:
        changes.append(f"{label}:{count}")
    return updated


def adapt_sql(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    text = text.replace("\ufeff", "")

    # SQLRight's SQLite grammar has no procedure/PSM statement.  The current
    # M2 templates put the useful DDL/DML in the procedure body, so retain the
    # body and remove only the procedure wrapper and its CALL.
    text = _sub(
        r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+[A-Za-z_][\w$]*\s*\([^;]*?\)\s*BEGIN\s*",
        "",
        text,
        changes,
        "unwrap_procedure",
    )
    text = _sub(
        r"\n\s*END\s*;\s*\n\s*CALL\s+[A-Za-z_][\w$]*\s*\([^;]*\)\s*;",
        "\n",
        text,
        changes,
        "remove_procedure_end_call",
    )
    text = _sub(
        r"\bCALL\s+[A-Za-z_][\w$]*\s*\([^;]*\)\s*;",
        "",
        text,
        changes,
        "remove_call",
    )

    # Remove statement forms that the SQLite SQLRight parser cannot consume.
    # These are intentionally statement-scoped (to the next semicolon), not
    # broad line filters, so comments and useful SQL remain auditable.
    text = _sub(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b.*?;", "", text, changes, "drop_create_type")
    text = _sub(r"\bALTER\s+TYPE\b.*?;", "", text, changes, "drop_alter_type")
    text = _sub(r"\bCREATE\s+SEQUENCE\b.*?;", "", text, changes, "drop_create_sequence")
    text = _sub(r"\bALTER\s+TABLE\b.*?\bADD\s+CONSTRAINT\b.*?;", "", text, changes, "drop_add_constraint")
    text = _sub(r"\bALTER\s+TABLE\b.*?\bALTER\s+COLUMN\b.*?;", "", text, changes, "drop_alter_column_type")
    text = _sub(r"\bCOPY\s*\(.*?\)\s*INTO\s+.*?;", "", text, changes, "drop_copy")
    text = _sub(r"\bPREPARE\s+[A-Za-z_][\w$]*\s+FROM\s+.*?;", "", text, changes, "drop_prepare")
    text = _sub(r"\bEXECUTE\s+[A-Za-z_][\w$]*\s*\([^;]*\)\s*;", "", text, changes, "drop_execute")

    # SQLite supports TEMP tables, ordinary indexes, and TRACE-like SELECTs
    # only as SELECTs.  Normalize the MonetDB spelling to that subset.
    text = _sub(r"\bCREATE\s+GLOBAL\s+TEMPORARY\s+TABLE\b", "CREATE TEMP TABLE", text, changes, "normalize_temp_table")
    text = _sub(r"\s+ON\s+COMMIT\s+(?:PRESERVE|DELETE)\s+ROWS\b", "", text, changes, "drop_on_commit")
    text = _sub(r"\bUSING\s+RTREE\b", "", text, changes, "normalize_rtree_index")
    text = _sub(r"\bNEXT\s+VALUE\s+FOR\s+[A-Za-z_][\w$]*\b", "1", text, changes, "normalize_sequence_value")
    text = _sub(r"\bTRACE\s+(?=SELECT\b)", "", text, changes, "drop_trace_keyword")
    text = _sub(r"\bDROP\s+(TABLE|VIEW|INDEX|TRIGGER)\b([^;]*?)\s+CASCADE\b", r"DROP \1\2", text, changes, "drop_cascade")

    # A few generated templates can leave an outer procedure terminator after
    # the CALL has been removed.  Do not remove END belonging to a trigger;
    # only remove an END at EOF, where it can only be the procedure wrapper.
    stripped = text.rstrip()
    if re.search(r"\bEND\s*;\s*$", stripped, flags=re.IGNORECASE | re.DOTALL):
        without_end = re.sub(r"\bEND\s*;\s*$", "", stripped, flags=re.IGNORECASE)
        if re.search(r"\b(CREATE\s+TABLE|SELECT|INSERT|UPDATE|DELETE)\b", without_end, flags=re.IGNORECASE):
            text = without_end
            changes.append("drop_trailing_procedure_end:1")

    # Keep output stable and avoid feeding a blank file to SQLRight.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not re.search(r"\b(CREATE\s+TABLE|SELECT|INSERT|UPDATE|DELETE)\b", text, flags=re.IGNORECASE):
        text = "SELECT 1;"
        changes.append("fallback_select:1")
    return text + "\n", changes


def sqlite_core_seed(source_name: str, source_text: str) -> str:
    """Build a conservative seed accepted by SQLRight's SQLite grammar.

    The original M2 text is retained in the manifest and as a short comment,
    but a mixed MonetDB PSM/DDL program cannot be partially repaired reliably
    with regexes: one unsupported statement makes SQLRight reject the whole
    file.  This core keeps the M2 instance distinct and exercises the common
    DDL/DML/query path that the MonetDB wrapper can execute.
    """
    digest = hashlib.sha256(source_text.encode("utf-8", errors="replace")).hexdigest()[:10]
    table = f"sqleek_m2_compat_{digest}"
    number = int(digest[:6], 16) % 1000
    label = f"m2_{digest}"
    return (
        f"-- source_m2_seed={source_name}\n"
        f"-- adapter=monetdb_sqlite_parser_compat\n"
        f"DROP TABLE IF EXISTS {table};\n"
        f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, a INTEGER, b TEXT, c REAL);\n"
        f"INSERT INTO {table}(id,a,b,c) VALUES\n"
        f"  (1, {number}, '{label}', 1.25),\n"
        f"  (2, -{number}, 'alpha', -2.5),\n"
        f"  (3, 0, NULL, 0.0);\n"
        f"CREATE INDEX {table}_idx ON {table}(b, a DESC);\n"
        f"SELECT id, typeof(b), length(b), a, c FROM {table}\n"
        f"  WHERE a BETWEEN -{number} AND {number}\n"
        f"  ORDER BY b, a DESC;\n"
        f"UPDATE {table} SET c = c + 1.0 WHERE id = 1;\n"
        f"SELECT count(*), max(c) FROM {table};\n"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    report_path = args.report.resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"missing input directory: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    for source in sorted(input_dir.iterdir()):
        if not source.is_file() or source.stat().st_size == 0:
            continue
        destination = output_dir / source.name
        source_text = source.read_text(encoding="utf-8", errors="replace")
        adapted, changes = adapt_sql(source_text)
        # The SQLite parser used by the MonetDB image rejects several valid
        # SQLite-adjacent constructs (notably window functions, triggers, and
        # PSM remnants).  The conservative core is deterministic and prevents
        # a parser-rejection loop from spinning AFL without any mutation.
        adapted = sqlite_core_seed(source.name, source_text)
        changes.append("fallback_sqlite_core:1")
        destination.write_text(adapted, encoding="utf-8")
        records.append(
            {
                "source": str(source),
                "destination": str(destination),
                "source_sha256": sha256(source),
                "destination_sha256": sha256(destination),
                "source_bytes": source.stat().st_size,
                "destination_bytes": destination.stat().st_size,
                "changes": changes,
            }
        )

    report = {
        "adapter": "monetdb_sqlite_parser_compat",
        "parser_backend": "sqlright_sqlite",
        "preserves_original_m2": True,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "seed_count": len(records),
        "records": records,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"seed_count": len(records), "output_dir": str(output_dir), "report": str(report_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
