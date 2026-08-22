#!/usr/bin/env python3
"""
Extract(Δ): Convert fix patch diff into structured feature vector ψ.

ψ = (A⁺, A⁻, C⁺, C⁻, G) where:
  A⁺ = added identifiers (field names, function names)
  A⁻ = removed identifiers
  C⁺ = added control flow structures (guards, checks)
  C⁻ = removed control flow structures
  G  = logical form of the new guard condition (the "fix invariant")
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/root/SQLeek")
PG_SRC = Path("/tmp/pg_src")
OUT = ROOT / "sqleek_pipeline" / "stage1_static" / "output" / "patch_features.json"

# Known fix patch for bug #19466 (Ayush Tiwari).
# Used when we cannot locate the commit in /tmp/pg_src history.
KNOWN_PATCH = r"""
--- a/src/backend/executor/execExpr.c
+++ b/src/backend/executor/execExpr.c
@@ -2011,8 +2011,21 @@
+                scratch.d.row.rowcache.cacheptr = NULL;
+                scratch.d.row.rowcache.tupdesc_id = 0;
+                typentry = lookup_type_cache(rowexpr->row_typeid,
+                                             TYPECACHE_TUPDESC);
+                tupdesc = CreateTupleDescCopyConstr(typentry->tupDesc);
+                scratch.d.row.rowcache.cacheptr = typentry;
+                scratch.d.row.rowcache.tupdesc_id = typentry->tupDesc_identifier;
-                tupdesc = lookup_rowtype_tupdesc_copy(rowexpr->row_typeid, -1);

--- a/src/backend/executor/execExprInterp.c
+++ b/src/backend/executor/execExprInterp.c
@@ -3667,6 +3667,17 @@
+    if (op->d.row.rowcache.tupdesc_id != 0)
+    {
+        TypeCacheEntry *typentry =
+            (TypeCacheEntry *) op->d.row.rowcache.cacheptr;
+        if (typentry->tupDesc_identifier != op->d.row.rowcache.tupdesc_id)
+            ereport(ERROR,
+                    (errcode(ERRCODE_DATATYPE_MISMATCH),
+                     errmsg("row type %s has changed",
+                            format_type_be(op->d.row.tupdesc->tdtypeid))));
+    }
"""


def get_patch_from_git() -> str:
    """Try to find the fix commit in /tmp/pg_src git history."""
    if not (PG_SRC / ".git").exists():
        return KNOWN_PATCH
    try:
        result = subprocess.run(
            ["git", "-C", str(PG_SRC), "log", "--all", "--oneline"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        keywords = ["rowtype", "eeop_row", "stale", "tupdesc", "rowcache", "ExprEvalRowtypeCache".lower()]
        for line in result.stdout.splitlines():
            if any(k in line.lower() for k in keywords):
                commit = line.split()[0]
                diff = subprocess.run(
                    ["git", "-C", str(PG_SRC), "show", commit, "--unified=5"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                print(f"[extract_patch_features] found candidate commit: {line}")
                return diff.stdout or KNOWN_PATCH
    except Exception as exc:
        print(f"[extract_patch_features] git lookup failed: {exc}")
    return KNOWN_PATCH


def extract_identifiers(patch_lines: list[str], prefix: str) -> set[str]:
    """Extract C identifiers from added (+) or removed (-) lines."""
    identifiers: set[str] = set()
    c_keywords = {
        "if",
        "else",
        "for",
        "while",
        "return",
        "int",
        "char",
        "void",
        "struct",
        "typedef",
        "static",
        "const",
        "NULL",
        "true",
        "false",
        "ERROR",
    }
    for line in patch_lines:
        if not line.startswith(prefix):
            continue
        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", line)
        for t in tokens:
            if t in c_keywords:
                continue
            if len(t) <= 2:
                continue
            identifiers.add(t)
    return identifiers


def extract_guard_condition(added_lines: list[str]) -> str:
    """Extract a compact logical form for added if-conditions."""
    guards: list[str] = []
    cache_terms = ["tupdesc", "typentry", "rowcache", "identifier", "cacheptr", "tupdesc_id"]
    for line in added_lines:
        m = re.search(r"if\s*\((.+?)\)", line)
        if not m:
            continue
        condition = m.group(1).strip()
        if any(t in condition.lower() for t in cache_terms):
            guards.append(condition)
    return " AND ".join(guards) if guards else "unknown"


def extract_structural_changes(added_lines: list[str], removed_lines: list[str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Extract coarse control-flow / API deltas."""
    c_plus: list[tuple[str, str]] = []
    c_minus: list[tuple[str, str]] = []

    for line in added_lines:
        if re.search(r"\bif\s*\(", line):
            c_plus.append(("guard", line.strip()))
        elif re.search(r"\bereport\s*\(", line):
            c_plus.append(("error_raise", line.strip()))
        elif "lookup_type_cache" in line:
            c_plus.append(("cache_lookup", line.strip()))

    for line in removed_lines:
        if re.search(r"\bif\s*\(", line):
            c_minus.append(("guard", line.strip()))
        elif "lookup_rowtype_tupdesc_copy" in line:
            c_minus.append(("old_tupdesc_lookup", line.strip()))

    return c_plus, c_minus


def extract(patch_text: str) -> dict:
    lines = patch_text.splitlines()
    added = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in lines if l.startswith("-") and not l.startswith("---")]

    a_plus = extract_identifiers(lines, "+")
    a_minus = extract_identifiers(lines, "-")
    a_plus_new = a_plus - a_minus

    c_plus, c_minus = extract_structural_changes(added, removed)
    g = extract_guard_condition(added)

    affected_files = sorted(set(re.findall(r"(?:---|\+\+\+)\s+[ab]/(\S+)", patch_text)))

    return {
        "A_plus": sorted(a_plus_new),
        "A_minus": sorted(a_minus - a_plus),
        "C_plus": c_plus,
        "C_minus": c_minus,
        "G": g,
        "affected_files": affected_files,
        "patch_stats": {"added_lines": len(added), "removed_lines": len(removed)},
        "patch_source": "git" if patch_text != KNOWN_PATCH else "known_patch_fallback",
    }


def main() -> dict:
    print("[extract_patch_features] extracting patch features (Extract(Δ))")
    patch = get_patch_from_git()
    psi = extract(patch)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(psi, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[extract_patch_features] wrote {OUT}")
    print(f"[extract_patch_features] A_plus={len(psi['A_plus'])} A_minus={len(psi['A_minus'])} C_plus={len(psi['C_plus'])} G={psi['G']!r}")
    return psi


if __name__ == "__main__":
    main()

