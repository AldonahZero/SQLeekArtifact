#!/usr/bin/env python3
"""Stage 0 pre-processing: automatically discover risky DBMS functions.

Inputs:
  - DBMS source tree, with git history when available.
  - A fix-like commit message regex.
  - Heuristic path coverage (no compiler instrumentation).

Outputs:
  - /root/SQLeek/sqleek_pipeline/stage0_pre_processing/output/<dbms>_priority_scores.json
  - /root/SQLeek/sqleek_pipeline/stage0_pre_processing/output/<dbms>_build.log

This stage is bug-id agnostic (no CVE / bug numbers). It mines fix-like
commits, extracts touched C functions from diff hunk contexts, and ranks
candidates. type_io name-pattern extras and deterministic tie-break reduce flat ties.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path


ROOT = Path("/root/SQLeek")
STAGE_DIR = ROOT / "sqleek_pipeline" / "stage0_pre_processing"
OUTPUT_DIR = STAGE_DIR / "output"
PG_SRC = Path("/tmp/pg_src")
SOURCE_ROOT = PG_SRC
CURRENT_DBMS = "postgres"
OUT = OUTPUT_DIR / "postgres_priority_scores.json"
LOOSE_OUT = OUTPUT_DIR / "postgres_priority_scores.loose.json"
STRICT_OUT = OUTPUT_DIR / "postgres_priority_scores.strict.json"
BUILD_LOG = OUTPUT_DIR / "postgres_build.log"
CONFIG_ENV = ROOT / "config.env"

DEFECT_CORPUS_GLOBS = [
    "/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/postgres/*.json",
]
SOURCE_SCOPE_RULES = {
    "executor": {
        "keywords": {"select", "join", "aggregate", "cursor", "fetch", "explain", "analyze", "executor", "tuple", "row", "record"},
        "patterns": ["src/backend/executor/**/*.c", "src/include/executor/**/*.h"],
    },
    "utils_adt": {
        "keywords": {"type", "rowtype", "record", "text", "varchar", "bytea", "encoding", "collation", "function", "cast"},
        "patterns": ["src/backend/utils/adt/**/*.c", "src/include/utils/**/*.h"],
    },
    "commands": {
        "keywords": {"alter", "create", "drop", "trigger", "table", "sequence", "index", "constraint", "view"},
        "patterns": ["src/backend/commands/**/*.c", "src/include/commands/**/*.h"],
    },
    "parser": {
        "keywords": {"parse", "parser", "syntax", "query", "sql"},
        "patterns": ["src/backend/parser/**/*.c", "src/include/parser/**/*.h"],
    },
    "optimizer": {
        "keywords": {"planner", "optimizer", "plan", "join", "view", "where", "order", "group"},
        "patterns": ["src/backend/optimizer/**/*.c", "src/include/optimizer/**/*.h"],
    },
    "access": {
        "keywords": {"index", "btree", "hash", "gin", "gist", "vacuum", "tuple", "heap"},
        "patterns": ["src/backend/access/**/*.c", "src/include/access/**/*.h"],
    },
    "storage": {
        "keywords": {"lock", "wal", "xlog", "buffer", "storage", "checkpoint", "shared memory"},
        "patterns": ["src/backend/storage/**/*.c", "src/include/storage/**/*.h"],
    },
    "memory": {
        "keywords": {"memory", "alloc", "palloc", "buffer overflow", "segmentation", "segfault", "core dump", "crash"},
        "patterns": ["src/backend/utils/mmgr/**/*.c", "src/include/utils/**/*.h"],
    },
    "tcop": {
        "keywords": {"backend", "connection", "query string", "portal", "transaction", "commit", "rollback"},
        "patterns": ["src/backend/tcop/**/*.c", "src/include/tcop/**/*.h"],
    },
    "catalog": {
        "keywords": {"catalog", "relation", "attribute", "schema", "namespace", "dependency"},
        "patterns": ["src/backend/catalog/**/*.c", "src/include/catalog/**/*.h"],
    },
}
POSTGRES_FALLBACK_SOURCE_PATTERNS = [
    "src/backend/executor/**/*.c",
    "src/backend/utils/adt/**/*.c",
    "src/include/executor/**/*.h",
    "src/include/utils/**/*.h",
]
RISK_FAMILY_RULES = [
    {
        "name": "executor_row_type_expression",
        "file_tokens": ("src/backend/executor/", "src/include/executor/"),
        "name_re": re.compile(r"^Exec(?:Eval|Just).*(?:Row|Field|Coerce|Convert|WholeRow|Var)", re.I),
        "semantic_signal": 0.020,
        "path_coverage": 0.15,
        "boost": 1.45,
    },
    {
        "name": "type_io_row_text",
        "file_tokens": ("src/backend/utils/adt/", "src/include/utils/"),
        "name_re": re.compile(r"(?:row|record|text|cstring).*(?:out|in|send|recv)|(?:out|in|send|recv)$|to_cstring", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.22,
        "boost": 1.35,
    },
    {
        "name": "stateful_portal_tuple_output",
        "file_tokens": ("src/backend/tcop/", "src/backend/executor/", "src/include/tcop/", "src/include/executor/"),
        "name_re": re.compile(r"(?:portal|cursor|printtup|tuplestore|slot|receiver|destreceiver)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.25,
        "boost": 1.30,
    },
    {
        "name": "catalog_type_cache",
        "file_tokens": ("src/backend/catalog/", "src/backend/utils/cache/", "src/include/catalog/", "src/include/utils/"),
        "name_re": re.compile(r"(?:typcache|relcache|tupdesc|tupledesc|rowtype|attribute|type)", re.I),
        "semantic_signal": 0.014,
        "path_coverage": 0.30,
        "boost": 1.20,
    },
]
MYSQL_SOURCE_SCOPE_RULES = {
    "sql_parse_and_lex": {
        "keywords": {"parse", "parser", "syntax", "lexer", "query", "sql", "statement"},
        "patterns": [
            "sql/sql_parse.cc",
            "sql/sql_lex*.cc",
            "sql/sql_yacc.yy",
            "sql/parse_tree/**/*.cc",
            "sql/item*.cc",
        ],
    },
    "optimizer_executor": {
        "keywords": {"select", "join", "optimizer", "planner", "executor", "where", "order", "group"},
        "patterns": [
            "sql/sql_select.cc",
            "sql/sql_optimizer.cc",
            "sql/sql_executor.cc",
            "sql/sql_planner.cc",
            "sql/range_optimizer/**/*.cc",
            "sql/join_optimizer/**/*.cc",
        ],
    },
    "ddl_dml_table": {
        "keywords": {"alter", "create", "drop", "insert", "update", "delete", "table", "trigger", "constraint"},
        "patterns": [
            "sql/sql_table.cc",
            "sql/sql_insert.cc",
            "sql/sql_update.cc",
            "sql/sql_delete.cc",
            "sql/sql_trigger.cc",
            "sql/table*.cc",
            "sql/field*.cc",
        ],
    },
    "transaction_state": {
        "keywords": {"transaction", "commit", "rollback", "lock", "cursor", "prepared"},
        "patterns": [
            "sql/transaction*.cc",
            "sql/sql_prepare.cc",
            "sql/handler.cc",
            "sql/lock*.cc",
        ],
    },
    "memory_string": {
        "keywords": {"memory", "alloc", "malloc", "buffer overflow", "segfault", "crash", "string"},
        "patterns": [
            "mysys/my_alloc.cc",
            "mysys/my_malloc.cc",
            "mysys/my_string.cc",
            "mysys/mulalloc.cc",
            "strings/**/*.cc",
            "sql/thr_malloc.cc",
        ],
    },
}
MYSQL_FALLBACK_SOURCE_PATTERNS = [
    "sql/sql_parse.cc",
    "sql/sql_select.cc",
    "sql/sql_optimizer.cc",
    "sql/sql_executor.cc",
    "sql/sql_planner.cc",
    "sql/sql_insert.cc",
    "sql/sql_update.cc",
    "sql/sql_delete.cc",
    "sql/sql_table.cc",
    "sql/item*.cc",
    "sql/field*.cc",
    "sql/table*.cc",
    "sql/range_optimizer/**/*.cc",
    "sql/join_optimizer/**/*.cc",
    "mysys/my_alloc.cc",
    "mysys/my_malloc.cc",
    "mysys/my_string.cc",
    "mysys/mulalloc.cc",
]
MYSQL_RISK_FAMILY_RULES = [
    {
        "name": "mysql_optimizer_executor",
        "file_tokens": ("sql/sql_select", "sql/sql_optimizer", "sql/sql_executor", "sql/sql_planner", "sql/range_optimizer/", "sql/join_optimizer/"),
        "name_re": re.compile(r"(?:JOIN|Query|SELECT|optimi[sz]e|execute|make_join|best_access|range|plan|iterator)", re.I),
        "semantic_signal": 0.020,
        "path_coverage": 0.25,
        "boost": 1.40,
    },
    {
        "name": "mysql_item_field_type",
        "file_tokens": ("sql/item", "sql/field", "sql/table"),
        "name_re": re.compile(r"(?:Item|Field|TABLE|row|record|store|val_|save|copy|convert|type)", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.30,
        "boost": 1.35,
    },
    {
        "name": "mysql_parser_state",
        "file_tokens": ("sql/sql_parse", "sql/sql_lex", "sql/parse_tree/"),
        "name_re": re.compile(r"(?:parse|lex|dispatch|mysql_execute|mysql_parse|Prepared|statement|command)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.28,
        "boost": 1.30,
    },
    {
        "name": "mysql_memory_string",
        "file_tokens": ("mysys/", "strings/", "sql/thr_malloc"),
        "name_re": re.compile(r"(?:alloc|malloc|free|mem|str|string|copy|append|pack|unpack|bitmap)", re.I),
        "semantic_signal": 0.015,
        "path_coverage": 0.35,
        "boost": 1.25,
    },
]
MARIADB_SOURCE_SCOPE_RULES = MYSQL_SOURCE_SCOPE_RULES
MARIADB_FALLBACK_SOURCE_PATTERNS = MYSQL_FALLBACK_SOURCE_PATTERNS
MARIADB_RISK_FAMILY_RULES = [
    {
        "name": "mariadb_optimizer_executor",
        "file_tokens": ("sql/sql_select", "sql/sql_optimizer", "sql/sql_executor", "sql/sql_planner", "sql/opt_", "sql/opt_range", "sql/opt_subselect"),
        "name_re": re.compile(r"(?:JOIN|Query|SELECT|optimi[sz]e|execute|make_join|best_access|range|plan|iterator)", re.I),
        "semantic_signal": 0.020,
        "path_coverage": 0.25,
        "boost": 1.40,
    },
    {
        "name": "mariadb_item_field_type",
        "file_tokens": ("sql/item", "sql/field", "sql/table"),
        "name_re": re.compile(r"(?:Item|Field|TABLE|row|record|store|val_|save|copy|convert|type)", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.30,
        "boost": 1.35,
    },
    {
        "name": "mariadb_parser_state",
        "file_tokens": ("sql/sql_parse", "sql/sql_lex", "sql/parse_tree/", "sql/sql_yacc"),
        "name_re": re.compile(r"(?:parse|lex|dispatch|mysql_execute|mysql_parse|Prepared|statement|command)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.28,
        "boost": 1.30,
    },
    {
        "name": "mariadb_ddl_dml_table",
        "file_tokens": ("sql/sql_table", "sql/sql_insert", "sql/sql_update", "sql/sql_delete", "sql/sql_trigger", "sql/table", "sql/field"),
        "name_re": re.compile(r"(?:alter|create|drop|insert|update|delete|trigger|table|field|constraint|metadata)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.30,
        "boost": 1.28,
    },
    {
        "name": "mariadb_memory_string",
        "file_tokens": ("mysys/", "strings/", "sql/thr_malloc"),
        "name_re": re.compile(r"(?:alloc|malloc|free|mem|str|string|copy|append|pack|unpack|bitmap)", re.I),
        "semantic_signal": 0.015,
        "path_coverage": 0.35,
        "boost": 1.25,
    },
]
SQLITE_SOURCE_SCOPE_RULES = {
    "parser_expr_select": {
        "keywords": {"parse", "parser", "syntax", "query", "sql", "select", "where", "join", "expression", "trigger"},
        "patterns": [
            "src/prepare.c",
            "src/tokenize.c",
            "src/resolve.c",
            "src/expr.c",
            "src/select.c",
            "src/where*.c",
            "src/build.c",
            "src/trigger.c",
            "src/alter.c",
            "src/pragma.c",
        ],
    },
    "vdbe_execution": {
        "keywords": {"vdbe", "bytecode", "opcode", "execute", "step", "cursor", "row", "record"},
        "patterns": [
            "src/vdbe*.c",
            "src/vdbe*.h",
        ],
    },
    "record_varint_decode": {
        "keywords": {"record", "serial", "varint", "decode", "column", "overflow", "corrupt"},
        "patterns": [
            "src/vdbe*.c",
            "src/util.c",
            "src/rowset.c",
            "src/btree.c",
        ],
    },
    "btree_pager_storage": {
        "keywords": {"btree", "pager", "page", "wal", "journal", "overflow", "transaction", "rollback"},
        "patterns": [
            "src/btree.c",
            "src/pager.c",
            "src/pcache*.c",
            "src/wal.c",
            "src/os*.c",
        ],
    },
    "memory_string": {
        "keywords": {"memory", "alloc", "malloc", "realloc", "buffer overflow", "segfault", "crash", "string"},
        "patterns": [
            "src/malloc.c",
            "src/mem*.c",
            "src/util.c",
            "src/printf.c",
            "src/utf.c",
        ],
    },
}
SQLITE_FALLBACK_SOURCE_PATTERNS = [
    "src/prepare.c",
    "src/tokenize.c",
    "src/resolve.c",
    "src/expr.c",
    "src/select.c",
    "src/where*.c",
    "src/build.c",
    "src/trigger.c",
    "src/alter.c",
    "src/pragma.c",
    "src/vdbe*.c",
    "src/btree.c",
    "src/pager.c",
    "src/pcache*.c",
    "src/wal.c",
    "src/malloc.c",
    "src/mem*.c",
    "src/util.c",
]
SQLITE_RISK_FAMILY_RULES = [
    {
        "name": "sqlite_vdbe_execution",
        "file_tokens": ("src/vdbe", "src/vdbeapi", "src/vdbeaux", "src/vdbemem", "src/vdbesort"),
        "name_re": re.compile(r"(?:sqlite3Vdbe|Vdbe|sqlite3_step|sqlite3_exec|sqlite3_prepare|opcode|Mem|Column|Cursor)", re.I),
        "semantic_signal": 0.020,
        "path_coverage": 0.25,
        "boost": 1.40,
    },
    {
        "name": "sqlite_record_varint_decode",
        "file_tokens": ("src/vdbe", "src/util.c", "src/rowset.c", "src/btree.c"),
        "name_re": re.compile(r"(?:Serial|Varint|Record|Column|Payload|sqlite3GetVarint|sqlite3PutVarint|sqlite3VdbeSerialGet|sqlite3Read)", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.30,
        "boost": 1.35,
    },
    {
        "name": "sqlite_parser_expr_select",
        "file_tokens": ("src/prepare", "src/tokenize", "src/resolve", "src/expr", "src/select", "src/where", "src/build", "src/trigger"),
        "name_re": re.compile(r"(?:parse|token|resolve|expr|select|where|trigger|sqlite3Prepare|sqlite3RunParser)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.28,
        "boost": 1.30,
    },
    {
        "name": "sqlite_btree_pager",
        "file_tokens": ("src/btree", "src/pager", "src/pcache", "src/wal", "src/os"),
        "name_re": re.compile(r"(?:Btree|Pager|Page|Wal|Journal|sqlite3Pager|sqlite3Btree|pcache|overflow)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.35,
        "boost": 1.28,
    },
    {
        "name": "sqlite_memory_string",
        "file_tokens": ("src/malloc", "src/mem", "src/util", "src/printf", "src/utf"),
        "name_re": re.compile(r"(?:alloc|malloc|realloc|free|mem|str|string|copy|printf|sqlite3DbMalloc|sqlite3Malloc)", re.I),
        "semantic_signal": 0.015,
        "path_coverage": 0.35,
        "boost": 1.25,
    },
]
MONETDB_SOURCE_SCOPE_RULES = {
    "sql_compiler_backend": {
        "keywords": {"parse", "parser", "syntax", "query", "sql", "select", "where", "join", "optimizer", "plan", "relation"},
        "patterns": [
            "sql/server/**/*.c",
            "sql/common/**/*.c",
            "sql/backends/monet5/**/*.c",
            "sql/include/**/*.h",
        ],
    },
    "mal_runtime_optimizer": {
        "keywords": {"mal", "optimizer", "interpreter", "module", "function", "instruction", "dataflow", "runtime"},
        "patterns": [
            "monetdb5/mal/**/*.c",
            "monetdb5/mal/**/*.h",
            "monetdb5/optimizer/**/*.c",
            "monetdb5/modules/**/*.c",
        ],
    },
    "gdk_bat_storage": {
        "keywords": {"bat", "column", "storage", "heap", "hash", "join", "select", "sort", "group", "aggregate", "candidate"},
        "patterns": [
            "gdk/**/*.c",
            "gdk/**/*.h",
        ],
    },
    "copy_stream_io": {
        "keywords": {"copy", "stream", "csv", "binary", "blob", "string", "utf8", "encoding", "buffer overflow", "crash"},
        "patterns": [
            "sql/backends/monet5/sql_bincopy*.c",
            "common/stream/**/*.c",
            "common/utils/**/*.c",
            "clients/mapilib/**/*.c",
        ],
    },
    "client_api_odbc": {
        "keywords": {"odbc", "client", "prepare", "execute", "fetch", "bind", "parameter", "descriptor", "connection"},
        "patterns": [
            "clients/odbc/driver/**/*.c",
            "clients/mapilib/**/*.c",
            "tools/monetdbe/**/*.c",
        ],
    },
}
MONETDB_FALLBACK_SOURCE_PATTERNS = [
    "sql/server/**/*.c",
    "sql/common/**/*.c",
    "sql/backends/monet5/**/*.c",
    "sql/include/**/*.h",
    "monetdb5/mal/**/*.c",
    "monetdb5/optimizer/**/*.c",
    "monetdb5/modules/**/*.c",
    "gdk/**/*.c",
    "common/stream/**/*.c",
    "common/utils/**/*.c",
    "clients/odbc/driver/**/*.c",
    "clients/mapilib/**/*.c",
    "tools/monetdbe/**/*.c",
]
MONETDB_RISK_FAMILY_RULES = [
    {
        "name": "monetdb_sql_compiler_backend",
        "file_tokens": ("sql/server/", "sql/common/", "sql/backends/monet5/", "sql/include/"),
        "name_re": re.compile(r"(?:sql_|mvc_|rel_|exp_|stmt_|query|parse|bind|optimi[sz]e|execute|select|join|project|aggr)", re.I),
        "semantic_signal": 0.020,
        "path_coverage": 0.25,
        "boost": 1.40,
    },
    {
        "name": "monetdb_mal_runtime_optimizer",
        "file_tokens": ("monetdb5/mal/", "monetdb5/optimizer/", "monetdb5/modules/"),
        "name_re": re.compile(r"(?:MAL|mal_|opt_|run|runtime|interpreter|dataflow|instruction|module|function|resolve)", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.30,
        "boost": 1.35,
    },
    {
        "name": "monetdb_gdk_bat_storage",
        "file_tokens": ("gdk/",),
        "name_re": re.compile(r"(?:BAT|GDK|BBP|Heap|hash|join|select|sort|group|cand|delta|storage|atom|str|copy)", re.I),
        "semantic_signal": 0.018,
        "path_coverage": 0.30,
        "boost": 1.35,
    },
    {
        "name": "monetdb_copy_stream_string",
        "file_tokens": ("sql/backends/monet5/sql_bincopy", "common/stream/", "common/utils/", "clients/mapilib/"),
        "name_re": re.compile(r"(?:copy|stream|read|write|buffer|blob|str|string|utf|mapi|parse|convert|append|pack|unpack)", re.I),
        "semantic_signal": 0.016,
        "path_coverage": 0.35,
        "boost": 1.28,
    },
    {
        "name": "monetdb_client_api_odbc",
        "file_tokens": ("clients/odbc/driver/", "clients/mapilib/", "tools/monetdbe/"),
        "name_re": re.compile(r"(?:SQL|ODBC|mapi|monetdbe|prepare|execute|fetch|bind|param|descriptor|connect|result)", re.I),
        "semantic_signal": 0.015,
        "path_coverage": 0.35,
        "boost": 1.25,
    },
]
FALLBACK_SOURCE_PATTERNS = POSTGRES_FALLBACK_SOURCE_PATTERNS
DBMS_PROFILES = {
    "postgres": {
        "source_root": PG_SRC,
        "clone_url": "https://github.com/postgres/postgres",
        "clone_depth": "200",
        "defect_globs": ["/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/postgres/*.json"],
        "source_scope_rules": SOURCE_SCOPE_RULES,
        "fallback_source_patterns": POSTGRES_FALLBACK_SOURCE_PATTERNS,
        "risk_family_rules": RISK_FAMILY_RULES,
    },
    "mysql": {
        "source_root": ROOT / "sources" / "mysql",
        "clone_url": None,
        "clone_depth": None,
        "defect_globs": ["/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/mysql/*.json"],
        "source_scope_rules": MYSQL_SOURCE_SCOPE_RULES,
        "fallback_source_patterns": MYSQL_FALLBACK_SOURCE_PATTERNS,
        "risk_family_rules": MYSQL_RISK_FAMILY_RULES,
    },
    "mariadb": {
        "source_root": ROOT / "sources" / "mariadb",
        "clone_url": "git@github.com:MariaDB/server.git",
        "clone_depth": "200",
        "defect_globs": ["/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/mariadb/*.json"],
        "source_scope_rules": MARIADB_SOURCE_SCOPE_RULES,
        "fallback_source_patterns": MARIADB_FALLBACK_SOURCE_PATTERNS,
        "risk_family_rules": MARIADB_RISK_FAMILY_RULES,
    },
    "sqlite": {
        "source_root": ROOT / "sources" / "sqlite",
        "clone_url": "git@github.com:sqlite/sqlite.git",
        "clone_depth": "200",
        "defect_globs": ["/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/sqlite/*.json"],
        "source_scope_rules": SQLITE_SOURCE_SCOPE_RULES,
        "fallback_source_patterns": SQLITE_FALLBACK_SOURCE_PATTERNS,
        "risk_family_rules": SQLITE_RISK_FAMILY_RULES,
    },
    "monetdb": {
        "source_root": ROOT / "sources" / "monetdb",
        "clone_url": "git@github.com:MonetDB/MonetDB.git",
        "clone_depth": "200",
        "defect_globs": ["/root/SQLeek/sqleek_pipeline/stage0_pre_processing/defect/monetdb/*.json"],
        "source_scope_rules": MONETDB_SOURCE_SCOPE_RULES,
        "fallback_source_patterns": MONETDB_FALLBACK_SOURCE_PATTERNS,
        "risk_family_rules": MONETDB_RISK_FAMILY_RULES,
    },
}
STATEFUL_SQL_TOKENS = {
    "begin",
    "commit",
    "rollback",
    "cursor",
    "fetch",
    "alter",
    "drop",
    "create",
    "prepared",
    "transaction",
    "portal",
}
FIX_RE = re.compile(r"fix|crash|overflow|null|segfault|CVE|rowtype|tupdesc|use-after-free|assert|corrupt|wrong result", re.I)
IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
STACK_FRAME_RE = re.compile(
    r"#\d+\s+.*?\bin\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s+at\s+([A-Za-z0-9_./+-]+\.(?:c|cc|cpp|cxx|h|hpp|hh)):(\d+)"
)
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "Assert",
    "StaticAssertDecl",
    "class",
    "namespace",
    "template",
    "operator",
}
THRESHOLD_MODES = {
    "loose": {
        "percentile": 0.75,
        "include_ties": True,
        "description": "P75 threshold; keep candidates tied at the threshold",
    },
    "strict": {
        "percentile": 0.90,
        "include_ties": False,
        "description": "P90 threshold; require priority strictly above the threshold",
    },
}


def configure_dbms(dbms: str) -> None:
    global CURRENT_DBMS, SOURCE_ROOT, DEFECT_CORPUS_GLOBS, SOURCE_SCOPE_RULES
    global FALLBACK_SOURCE_PATTERNS, RISK_FAMILY_RULES, OUT, LOOSE_OUT, STRICT_OUT, BUILD_LOG

    if dbms not in DBMS_PROFILES:
        valid = ", ".join(sorted(DBMS_PROFILES))
        raise ValueError(f"unsupported Stage 0 DBMS={dbms!r}; expected one of: {valid}")

    profile = DBMS_PROFILES[dbms]
    CURRENT_DBMS = dbms
    SOURCE_ROOT = Path(profile["source_root"])
    DEFECT_CORPUS_GLOBS = list(profile["defect_globs"])
    SOURCE_SCOPE_RULES = profile["source_scope_rules"]
    FALLBACK_SOURCE_PATTERNS = list(profile["fallback_source_patterns"])
    RISK_FAMILY_RULES = profile["risk_family_rules"]

    OUT = OUTPUT_DIR / f"{dbms}_priority_scores.json"
    LOOSE_OUT = OUTPUT_DIR / f"{dbms}_priority_scores.loose.json"
    STRICT_OUT = OUTPUT_DIR / f"{dbms}_priority_scores.strict.json"
    BUILD_LOG = OUTPUT_DIR / f"{dbms}_build.log"


def legacy_postgres_outputs() -> dict[str, Path]:
    return {
        "priority_scores": OUTPUT_DIR / "priority_scores.json",
        "priority_scores_loose": OUTPUT_DIR / "priority_scores.loose.json",
        "priority_scores_strict": OUTPUT_DIR / "priority_scores.strict.json",
        "log": OUTPUT_DIR / "build.log",
    }


def log(msg: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with BUILD_LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg.rstrip() + "\n")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        encoding="utf-8",
        errors="replace",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log(proc.stdout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}")
    return proc


def load_config_env() -> dict[str, str]:
    config: dict[str, str] = {}
    if not CONFIG_ENV.exists():
        return config
    for raw_line in CONFIG_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def threshold_mode_from_config() -> tuple[str, dict[str, object]]:
    mode = load_config_env().get("STAGE0_THRESHOLD_MODE", "loose").lower()
    if mode not in THRESHOLD_MODES:
        valid = ", ".join(sorted(THRESHOLD_MODES))
        raise ValueError(f"invalid STAGE0_THRESHOLD_MODE={mode!r}; expected one of: {valid}")
    return mode, THRESHOLD_MODES[mode]


def tie_break_scale_from_config() -> float:
    raw = load_config_env().get("STAGE0_TIE_BREAK_SCALE", "").strip()
    if not raw:
        return 1.5e-5
    return float(raw)


def deterministic_tie_break(function_name: str, scale: float) -> float:
    """Stable [0, scale) perturbation from function name (reproducible across runs)."""
    if scale <= 0.0:
        return 0.0
    digest = hashlib.sha256(function_name.encode("utf-8")).digest()
    u01 = int.from_bytes(digest[:8], "big", signed=False) / float(2**64)
    return u01 * scale


def type_io_intrinsic_risk_bonus(function_name: str, risk_family_names: list[str]) -> float:
    """Finer weight inside type_io_row_text: hot composite/text paths get a small extra risk_signal."""
    if "type_io_row_text" not in risk_family_names:
        return 0.0
    fn = function_name
    if fn == "record_out" or re.search(r"(?:^|_)to_cstring\b", fn):
        return 0.0012
    if fn.startswith("record_") and fn.endswith("_out"):
        return 0.00035
    if re.search(r"(?:^|_)text(?:out|in|send|recv)\b", fn) or re.search(r"(?:^|_)cstring\b", fn):
        return 0.0002
    return 0.0


def ensure_source() -> str:
    profile = DBMS_PROFILES[CURRENT_DBMS]
    if (SOURCE_ROOT / ".git").exists():
        log(f"[stage0] reuse existing git source for {CURRENT_DBMS}: {SOURCE_ROOT}")
        return "reused_git"
    if SOURCE_ROOT.exists():
        log(f"[stage0] use source snapshot without git history for {CURRENT_DBMS}: {SOURCE_ROOT}")
        return "source_snapshot_no_git"

    clone_url = profile.get("clone_url")
    if not clone_url:
        raise RuntimeError(f"missing source tree for {CURRENT_DBMS}: {SOURCE_ROOT}")

    clone_depth = str(profile.get("clone_depth") or "200")
    proc = run(
        ["git", "clone", f"--depth={clone_depth}", str(clone_url), str(SOURCE_ROOT)],
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not clone {CURRENT_DBMS} source for automatic candidate discovery")
    return "cloned"


def git_pathspecs(source_patterns: list[str]) -> list[str]:
    return [f":(glob){pattern}" for pattern in source_patterns]


def matching_commits(source_patterns: list[str]) -> list[dict[str, str]]:
    if not (SOURCE_ROOT / ".git").exists():
        log(f"[stage0] skip git commit mining: {SOURCE_ROOT} is not a git repository")
        return []
    proc = run(
        [
            "git",
            "log",
            "--format=%H%x00%s",
            "--",
            *git_pathspecs(source_patterns),
        ],
        cwd=SOURCE_ROOT,
        check=False,
    )
    commits: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if "\x00" not in line:
            continue
        sha, subject = line.split("\x00", 1)
        if FIX_RE.search(subject):
            commits.append({"sha": sha, "subject": subject})
    return commits


def new_candidate() -> dict:
    return {
        "direct_fix_commits": set(),
        "file_risk_commits": set(),
        "defect_report_ids": set(),
        "semantic_family_signal": 0.0,
        "risk_families": set(),
        "files": set(),
        "example_commits": [],
        "example_defect_mentions": [],
        "discovery_sources": set(),
    }


def extract_function_from_context(context: str) -> str | None:
    """Best-effort extraction from git hunk xfuncname context."""
    matches = IDENT_RE.findall(context)
    for name in reversed(matches):
        if name not in KEYWORDS:
            return name
    return None


def extract_function_from_definition(line: str) -> str | None:
    """Fallback for function definitions present in diff context lines."""
    stripped = line.lstrip(" +-")
    if not stripped or stripped.startswith(("#", "/*", "*")):
        return None
    if stripped.endswith(";"):
        return None
    if "typedef" in stripped:
        return None
    match = re.match(
        r"(?:template\s*<[^>]+>\s*)?"
        r"(?:(?:static|inline|extern|virtual|constexpr|consteval|friend|MYSQL_ATTRIBUTE)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:<>,\s\*&~]+\s+)?"
        r"(?:(?:[A-Za-z_][A-Za-z0-9_]*::)*)"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?(?:\{|$)",
        stripped,
    )
    if not match:
        return None
    name = match.group(1)
    if name in KEYWORDS:
        return None
    return name


def add_commit_example(entry: dict, commit: dict[str, str], file_path: str) -> None:
    if len(entry["example_commits"]) < 3:
        entry["example_commits"].append({
            "sha": commit["sha"],
            "subject": commit["subject"],
            "file": file_path,
        })


def discover_touched_functions(
    commits: list[dict[str, str]],
    source_patterns: list[str],
) -> tuple[dict[str, dict], dict[str, set[str]]]:
    candidates: dict[str, dict] = {}
    file_commits: dict[str, set[str]] = defaultdict(set)
    for commit in commits:
        sha = commit["sha"]
        proc = run(
            [
                "git",
                "show",
                "--format=",
                "--unified=0",
                "--function-context",
                sha,
                "--",
                *git_pathspecs(source_patterns),
            ],
            cwd=SOURCE_ROOT,
            check=False,
        )

        current_file = ""
        functions_in_commit: set[tuple[str, str]] = set()
        for line in proc.stdout.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else ""
                if current_file:
                    file_commits[current_file].add(sha)
                continue

            if line.startswith("@@"):
                context = line.rsplit("@@", 1)[-1].strip()
                fn = extract_function_from_context(context)
                if fn and current_file:
                    functions_in_commit.add((fn, current_file))
                continue

            fn = extract_function_from_definition(line)
            if fn and current_file:
                functions_in_commit.add((fn, current_file))

        for fn, file_path in functions_in_commit:
            entry = candidates.setdefault(fn, new_candidate())
            entry["direct_fix_commits"].add(sha)
            entry["files"].add(file_path)
            entry["discovery_sources"].add("direct_diff_hunk")
            add_commit_example(entry, commit, file_path)

    return candidates, file_commits


def extract_functions_from_source(file_path: Path) -> set[str]:
    if not file_path.exists() or file_path.suffix not in SOURCE_SUFFIXES:
        return set()
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    functions: set[str] = set()
    for idx, line in enumerate(lines):
        name = extract_function_from_definition(line)
        if not name:
            continue
        next_idx = idx + 1
        while next_idx < len(lines) and not lines[next_idx].strip():
            next_idx += 1
        if "{" in line or (next_idx < len(lines) and lines[next_idx].strip().startswith("{")):
            functions.add(name)
    return functions


def apply_file_risk_propagation(
    candidates: dict[str, dict],
    file_commits: dict[str, set[str]],
    commit_by_sha: dict[str, dict[str, str]],
) -> None:
    for file_path, shas in file_commits.items():
        full_path = SOURCE_ROOT / file_path
        for fn in extract_functions_from_source(full_path):
            entry = candidates.setdefault(fn, new_candidate())
            entry["file_risk_commits"].update(shas)
            entry["files"].add(file_path)
            entry["discovery_sources"].add("same_file_risk_propagation")
            for sha in sorted(shas):
                commit = commit_by_sha.get(sha)
                if commit:
                    add_commit_example(entry, commit, file_path)


def defect_corpus_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in DEFECT_CORPUS_GLOBS:
        for raw in glob.glob(pattern):
            path = Path(raw)
            key = str(path.resolve())
            if path.is_file() and key not in seen:
                seen.add(key)
                paths.append(path)
    return sorted(paths)


def load_defect_corpus_strings() -> list[str]:
    strings: list[str] = []
    for defect_path in defect_corpus_paths():
        try:
            payload = json.loads(defect_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = defect_path.read_text(encoding="utf-8", errors="replace")
        strings.extend(walk_json_strings(payload))
    return strings


def defect_corpus_text() -> str:
    return "\n".join(load_defect_corpus_strings()).lower()


def statefulness_boost_from_defects(corpus_text: str) -> tuple[float, dict[str, int]]:
    hits = {token: corpus_text.count(token) for token in STATEFUL_SQL_TOKENS if corpus_text.count(token) > 0}
    if len(hits) >= 3:
        return 1.25, hits
    if hits:
        return 1.10, hits
    return 1.0, hits


def infer_source_scope_from_defects(corpus_text: str) -> tuple[list[str], dict[str, dict[str, object]]]:
    selected: list[str] = []
    evidence: dict[str, dict[str, object]] = {}
    for subsystem, rule in SOURCE_SCOPE_RULES.items():
        hits = {
            keyword: corpus_text.count(keyword)
            for keyword in rule["keywords"]
            if corpus_text.count(keyword) > 0
        }
        if not hits:
            continue
        selected.extend(rule["patterns"])
        evidence[subsystem] = {
            "keyword_hits": hits,
            "patterns": rule["patterns"],
        }

    # Keep Stage 0 useful even for a sparse corpus, while still avoiding all-src.
    if not selected:
        selected = [
            *FALLBACK_SOURCE_PATTERNS,
        ]
        evidence["fallback_core_execution_and_types"] = {
            "keyword_hits": {},
            "patterns": selected,
        }

    return sorted(set(selected)), evidence


def risk_family_profile(function_name: str, files: set[str]) -> dict[str, object]:
    matched: list[str] = []
    semantic_signal = 0.0
    path_coverage = None
    boost = 1.0
    joined_files = " ".join(files)

    for rule in RISK_FAMILY_RULES:
        if not any(token in joined_files for token in rule["file_tokens"]):
            continue
        if not rule["name_re"].search(function_name):
            continue
        matched.append(rule["name"])
        semantic_signal = max(semantic_signal, float(rule["semantic_signal"]))
        path_coverage = min(path_coverage, float(rule["path_coverage"])) if path_coverage is not None else float(rule["path_coverage"])
        boost = max(boost, float(rule["boost"]))

    return {
        "families": matched,
        "semantic_signal": semantic_signal,
        "path_coverage": path_coverage,
        "boost": boost,
    }


def apply_risk_family_expansion(candidates: dict[str, dict], function_index: dict[str, set[str]]) -> None:
    for fn, files in function_index.items():
        profile = risk_family_profile(fn, files)
        if not profile["families"]:
            continue
        entry = candidates.setdefault(fn, new_candidate())
        entry["files"].update(files)
        entry["semantic_family_signal"] = max(entry["semantic_family_signal"], float(profile["semantic_signal"]))
        entry["risk_families"].update(profile["families"])
        entry["discovery_sources"].add("risk_family_from_defect_scope")


def walk_json_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(walk_json_strings(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for item in value.values():
            strings.extend(walk_json_strings(item))
        return strings
    return []


def source_function_index(source_patterns: list[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for pattern in source_patterns:
        for raw in SOURCE_ROOT.glob(pattern):
            rel = str(raw.relative_to(SOURCE_ROOT))
            for fn in extract_functions_from_source(raw):
                index[fn].add(rel)
    return index


def iter_defect_records(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def apply_defect_corpus_frequency(candidates: dict[str, dict], function_index: dict[str, set[str]]) -> int:
    """Map workflow P3: BugFrequency(f) ≈ |{ b ∈ BugReports | f 出现在 b 的证据 }| / |BugReports|.

    优先使用 gdb 风格栈帧；若该条报告无任何栈帧（LLM 清洗语料常见），则回退为整段文本中的已知函数符号，
    以便分母仍为独立 bug 条数、分子仍为「至少被一条报告关联的函数」。
    """
    known_functions = set(function_index)
    bug_report_total = 0
    for defect_path in defect_corpus_paths():
        try:
            payload = json.loads(defect_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue

        for idx, record in enumerate(iter_defect_records(payload)):
            bug_report_total += 1
            rid = f"{defect_path.stem}:{record.get('id', idx)}"
            texts = walk_json_strings(record)
            stack_functions: set[str] = set()
            for text in texts:
                for match in STACK_FRAME_RE.finditer(text):
                    fn = match.group(1)
                    if fn in known_functions:
                        stack_functions.add(fn)

            if stack_functions:
                for fn in stack_functions:
                    entry = candidates.setdefault(fn, new_candidate())
                    if rid in entry["defect_report_ids"]:
                        continue
                    entry["defect_report_ids"].add(rid)
                    entry["files"].update(function_index.get(fn, set()))
                    entry["discovery_sources"].add("defect_corpus_stack_frame")
                    if len(entry["example_defect_mentions"]) < 3:
                        entry["example_defect_mentions"].append({
                            "report_id": rid,
                            "source": str(defect_path),
                            "function": fn,
                            "evidence": "stack_frame",
                        })
                continue

            combined = "\n".join(texts)
            mentioned = {token for token in IDENT_RE.findall(combined) if token in known_functions}
            for fn in mentioned:
                entry = candidates.setdefault(fn, new_candidate())
                if rid in entry["defect_report_ids"]:
                    continue
                entry["defect_report_ids"].add(rid)
                entry["files"].update(function_index[fn])
                entry["discovery_sources"].add("defect_corpus_report_text_fallback")
                if len(entry["example_defect_mentions"]) < 3:
                    entry["example_defect_mentions"].append({
                        "report_id": rid,
                        "source": str(defect_path),
                        "function": fn,
                        "evidence": "text_fallback",
                        "context": combined[:240],
                    })
    return bug_report_total


def estimate_path_coverage(function_name: str, files: set[str]) -> tuple[float, str]:
    """Heuristic estimate of how well regression tests exercise this code path (no instrumentation)."""
    profile = risk_family_profile(function_name, files)
    if profile["path_coverage"] is not None:
        return float(profile["path_coverage"]), f"heuristic: path-level coverage for risk family {','.join(profile['families'])}"

    joined = " ".join(files).lower()
    lowered = function_name.lower()

    if "/test/" in joined or "/regress/" in joined:
        return 0.75, "heuristic: test/regress code is likely exercised"
    if any(token in joined for token in ("executor", "access/", "parser", "commands/")):
        return 0.35, "heuristic: backend execution paths are partially covered by regression tests"
    if any(token in joined for token in ("utils/adt", "utils/cache", "utils/mmgr")):
        return 0.45, "heuristic: shared backend utility path with moderate test coverage"
    if any(token in lowered for token in ("out", "recv", "send", "cstring", "row", "record")):
        return 0.40, "heuristic: type I/O and row paths have uneven SQL regression coverage"
    return 0.50, "heuristic: default unknown coverage"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = math.floor((len(ordered) - 1) * p)
    return ordered[idx]


def select_functions(rows: dict[str, dict], theta: float, include_ties: bool) -> list[str]:
    if include_ties:
        selected = [fn for fn, data in rows.items() if float(data["priority"]) >= theta and float(data["priority"]) > 0]
    else:
        selected = [fn for fn, data in rows.items() if float(data["priority"]) > theta]
    return sorted(selected, key=lambda fn: float(rows[fn]["priority"]), reverse=True)


def add_family_representatives(rows: dict[str, dict], selected: list[str], min_per_family: int) -> list[str]:
    selected_set = set(selected)
    counts: dict[str, int] = defaultdict(int)
    pools: dict[str, list[str]] = defaultdict(list)

    for fn in selected:
        for family in rows.get(fn, {}).get("risk_families", []) or []:
            counts[family] += 1

    for fn, data in rows.items():
        if float(data["priority"]) <= 0:
            continue
        for family in data.get("risk_families", []) or []:
            pools[family].append(fn)

    added: list[str] = []
    for family in sorted(pools):
        for fn in sorted(pools[family], key=lambda name: float(rows[name]["priority"]), reverse=True):
            if counts[family] >= min_per_family:
                break
            if fn in selected_set:
                continue
            selected_set.add(fn)
            added.append(fn)
            for matched_family in rows[fn].get("risk_families", []) or []:
                counts[matched_family] += 1

    if not added:
        return selected
    return sorted([*selected, *added], key=lambda fn: float(rows[fn]["priority"]), reverse=True)


def result_for_mode(
    *,
    mode: str,
    mode_config: dict[str, object],
    active_mode: str,
    clone_status: str,
    source_patterns: list[str],
    scope_evidence: dict[str, dict[str, object]],
    statefulness_hits: dict[str, int],
    commits: list[dict[str, str]],
    file_commits: dict[str, set[str]],
    defect_corpus_bug_report_count: int,
    statefulness_boost: float,
    rows: dict[str, dict],
    priorities: list[float],
    ranking_tuning: dict[str, object],
) -> dict:
    theta = percentile(priorities, float(mode_config["percentile"]))
    selected = select_functions(rows, theta, bool(mode_config["include_ties"]))
    if CURRENT_DBMS == "monetdb":
        selected = add_family_representatives(rows, selected, min_per_family=4)
    output_functions = rows if mode == "loose" else {
        fn: rows[fn]
        for fn in selected
    }

    return {
        "stage": "stage0_pre_processing",
        "dbms": CURRENT_DBMS,
        "candidate_discovery": "automatic_from_fix_like_git_commits_plus_file_risk_and_defect_corpus",
        "clone_status": clone_status,
        "config": {
            "config_env": str(CONFIG_ENV),
            "active_threshold_mode": active_mode,
            "threshold_mode": mode,
            "threshold_mode_config": mode_config,
            "available_threshold_modes": sorted(THRESHOLD_MODES),
        },
        "inputs": {
            "source_repo": str(SOURCE_ROOT),
            "source_scope_method": "defect_corpus_keyword_to_subsystem_mapping",
            "source_paths": source_patterns,
            "source_scope_evidence": scope_evidence,
            "statefulness_keyword_hits": statefulness_hits,
            "defect_corpus": [str(path) for path in defect_corpus_paths()],
            "message_regex": FIX_RE.pattern,
            "coverage_source": "heuristic path coverage (see path_coverage / coverage_source per function)",
            "ranking_tuning": ranking_tuning,
        },
        "outputs": {
            "priority_scores": str(OUT),
            "priority_scores_loose": str(LOOSE_OUT),
            "priority_scores_strict": str(STRICT_OUT),
            "log": str(BUILD_LOG),
            "legacy_postgres_compat": {
                key: str(path)
                for key, path in legacy_postgres_outputs().items()
            } if CURRENT_DBMS == "postgres" else None,
        },
        "matching_commit_count": len(commits),
        "risky_file_count": len(file_commits),
        "defect_corpus_bug_report_count": defect_corpus_bug_report_count,
        "statefulness_boost": statefulness_boost,
        "candidate_function_count": len(rows),
        "output_function_count": len(output_functions),
        "theta_priority_method": mode_config["description"],
        "theta_priority": round(theta, 6),
        "functions": output_functions,
        "selected_functions": selected,
        "notes": [
            "Function names are extracted from git diff hunk contexts, same-file risk propagation, risk-family expansion, and the provided defect JSON corpus.",
            "type_io_row_text uses small name-pattern extras; STAGE0_TIE_BREAK_SCALE breaks exact ties deterministically.",
            "This stage does not read validation crash_stack*.txt files.",
            "TaintReach is not computed in Stage 0; Stage 1 CodeQL supplies 0/1 after analysis (see taint_reach_prior in each row).",
        ],
    }


def serialise_candidates(
    candidates: dict[str, dict],
    t_window: int,
    defect_corpus_bug_report_count: int,
    statefulness_boost: float,
    tie_break_scale: float,
) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for fn, data in candidates.items():
        direct_fix_count = len(data["direct_fix_commits"])
        file_risk_count = len(data["file_risk_commits"])
        defect_reports_with_f = len(data["defect_report_ids"])
        semantic_signal = float(data["semantic_family_signal"])
        direct_signal = direct_fix_count / max(t_window, 1)
        file_signal = file_risk_count / max(t_window, 1)
        bug_frequency = defect_reports_with_f / max(defect_corpus_bug_report_count, 1)
        defect_signal = bug_frequency
        files = sorted(data["files"])
        profile = risk_family_profile(fn, set(files))
        family_boost = float(profile["boost"])
        applied_statefulness_boost = statefulness_boost if profile["families"] else 1.0
        families_list = sorted(data["risk_families"] or profile["families"])
        intrinsic_bonus = type_io_intrinsic_risk_bonus(fn, families_list)
        risk_core = direct_signal + 0.5 * file_signal + 0.25 * defect_signal + semantic_signal
        risk_signal = risk_core + intrinsic_bonus
        path_coverage, coverage_reason = estimate_path_coverage(fn, set(files))
        priority_base = risk_signal * (1.0 - path_coverage) * 1.0 * family_boost * applied_statefulness_boost
        tie = deterministic_tie_break(fn, tie_break_scale)
        priority = priority_base + tie
        rows[fn] = {
            "direct_fix_commits": direct_fix_count,
            "file_risk_commits": file_risk_count,
            "defect_reports_with_f": defect_reports_with_f,
            "defect_mentions": defect_reports_with_f,
            "bug_frequency": round(bug_frequency, 6),
            "semantic_family_signal": round(semantic_signal, 6),
            "T_matching_commits": t_window,
            "direct_fix_commits_over_T": round(direct_signal, 6),
            "file_risk_commits_over_T": round(file_signal, 6),
            "defect_mentions_over_total": round(defect_signal, 6),
            "intrinsic_risk_bonus": round(intrinsic_bonus, 6),
            "risk_signal": round(risk_signal, 6),
            "path_coverage": path_coverage,
            "coverage_source": coverage_reason,
            "risk_families": families_list,
            "risk_family_boost": round(family_boost, 6),
            "statefulness_boost": round(applied_statefulness_boost, 6),
            "taint_reach_prior": 1,
            "taint_reach_note": "initialized to 1 in Stage 0; multiply by CodeQL 0/1 after Stage 1 if matching workflow.md P4",
            "priority_base": round(priority_base, 6),
            "tie_break": round(tie, 8),
            "priority": round(priority, 6),
            "calculation": (
                f"risk_core={risk_core:.6f} +intrinsic={intrinsic_bonus:.6f} -> "
                f"risk={risk_signal:.6f}; base={priority_base:.6f} +tie={tie:.8f} = {priority:.6f}"
            ),
            "files": files,
            "example_commits": data["example_commits"],
            "example_defect_mentions": data["example_defect_mentions"],
            "discovery_sources": sorted(data["discovery_sources"]),
        }
    return rows


def parse_args() -> argparse.Namespace:
    config = load_config_env()
    default_dbms = config.get("STAGE0_DBMS", "postgres").lower()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbms", choices=sorted(DBMS_PROFILES), default=default_dbms)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    configure_dbms(args.dbms)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"\n[stage0] Automatic pre-processing for {CURRENT_DBMS}: discover and rank touched functions")
    clone_status = ensure_source()
    threshold_mode, threshold_config = threshold_mode_from_config()
    corpus_text = defect_corpus_text()
    statefulness_boost, statefulness_hits = statefulness_boost_from_defects(corpus_text)
    source_patterns, scope_evidence = infer_source_scope_from_defects(corpus_text)
    commits = matching_commits(source_patterns)
    candidates, file_commits = discover_touched_functions(commits, source_patterns)
    apply_file_risk_propagation(candidates, file_commits, {commit["sha"]: commit for commit in commits})
    function_index = source_function_index(source_patterns)
    apply_risk_family_expansion(candidates, function_index)
    defect_corpus_bug_report_count = apply_defect_corpus_frequency(candidates, function_index)
    tie_break_scale = tie_break_scale_from_config()
    ranking_tuning = {
        "tie_break_scale": tie_break_scale,
        "type_io_intrinsic": "record_out / *to_cstring +0.0012; record_*_out +0.00035; text* I/O +0.0002",
    }
    rows = serialise_candidates(
        candidates,
        len(commits),
        defect_corpus_bug_report_count,
        statefulness_boost,
        tie_break_scale,
    )
    priorities = [float(data["priority"]) for data in rows.values()]
    results = {
        mode: result_for_mode(
            mode=mode,
            mode_config=mode_config,
            active_mode=threshold_mode,
            clone_status=clone_status,
            source_patterns=source_patterns,
            scope_evidence=scope_evidence,
            statefulness_hits=statefulness_hits,
            commits=commits,
            file_commits=file_commits,
            defect_corpus_bug_report_count=defect_corpus_bug_report_count,
            statefulness_boost=statefulness_boost,
            rows=rows,
            priorities=priorities,
            ranking_tuning=ranking_tuning,
        )
        for mode, mode_config in THRESHOLD_MODES.items()
    }

    LOOSE_OUT.write_text(json.dumps(results["loose"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    STRICT_OUT.write_text(json.dumps(results["strict"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT.write_text(json.dumps(results[threshold_mode], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[stage0] wrote {LOOSE_OUT}")
    log(f"[stage0] wrote {STRICT_OUT}")
    log(f"[stage0] wrote active output {OUT} from mode={threshold_mode}")

    if CURRENT_DBMS == "postgres":
        legacy = legacy_postgres_outputs()
        legacy["priority_scores_loose"].write_text(LOOSE_OUT.read_text(encoding="utf-8"), encoding="utf-8")
        legacy["priority_scores_strict"].write_text(STRICT_OUT.read_text(encoding="utf-8"), encoding="utf-8")
        legacy["priority_scores"].write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8")
        legacy["log"].write_text(BUILD_LOG.read_text(encoding="utf-8"), encoding="utf-8")
        log("[stage0] refreshed legacy PostgreSQL compatibility outputs")


if __name__ == "__main__":
    main()
