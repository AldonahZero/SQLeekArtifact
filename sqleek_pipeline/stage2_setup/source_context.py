"""Source-code context retrieval for LLM seed inference."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqleek_pipeline.stage2_setup.common import TARGETS_DIR


def get_source_context(fn_name: str, src_root: Path) -> str:
    """Search <src_root> for a function definition and return ~30 lines around it."""
    if not src_root.exists():
        return f"// source root missing: {src_root} (cannot find {fn_name})"

    needle = re.compile(rf"\b{re.escape(fn_name)}\s*\(")
    candidates: list[tuple[Path, int, str]] = []
    for p in src_root.rglob("*.c"):
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if not needle.search(line):
                continue
            if line.strip().endswith(";"):
                continue
            candidates.append((p, i, line))
            if len(candidates) >= 20:
                break
        if len(candidates) >= 20:
            break

    if not candidates:
        return f"// Source not found for {fn_name} under {src_root}"

    chosen = _choose_definition_candidate(candidates)
    filepath, lineno0, _ = chosen
    lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(0, lineno0 - 2)
    end = min(len(lines), lineno0 + 30)
    return "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))


def load_hotspot_contexts(dbms: str, bug_type: str, src_root: Path, max_hotspots: int = 8) -> dict[str, str]:
    """Load Stage 1 hotspot file (<dbms>_<bug_type>.txt) and return source contexts."""
    hotspot_file = TARGETS_DIR / f"{dbms}_{bug_type}.txt"
    if not hotspot_file.exists():
        return {}

    out: dict[str, str] = {}
    raw = hotspot_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    picked = 0
    for line in raw:
        if picked >= max_hotspots:
            break
        line = line.strip()
        if not line or ":" not in line:
            continue
        base, lineno_s = line.rsplit(":", 1)
        try:
            lineno = int(lineno_s)
        except Exception:
            continue
        src_file = _find_source_file_by_basename(src_root, base)
        if src_file is None:
            continue
        key = f"HOTSPOT {base}:{lineno}"
        out[key] = _get_file_line_context(src_file, lineno, window=22)
        picked += 1
    return out


def top_functions_for_context(chain_list: list[dict[str, Any]], k: int = 3) -> list[str]:
    # Keep historical behavior: sort by depth ascending and take the first k functions.
    chain_sorted = sorted(chain_list, key=lambda x: int(x.get("depth") or 999))
    out: list[str] = []
    for c in chain_sorted[:k]:
        fn = c.get("danger_fn")
        if isinstance(fn, str) and fn:
            out.append(fn)
    return out


def _choose_definition_candidate(candidates: list[tuple[Path, int, str]]) -> tuple[Path, int, str]:
    for p, i, line in candidates:
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        window = "\n".join(lines[i : min(len(lines), i + 5)])
        if "{" in window:
            return (p, i, line)
    return candidates[0]


def _find_source_file_by_basename(src_root: Path, basename: str) -> Path | None:
    """Best-effort locate a source file under <src_root> by its basename."""
    best: Path | None = None
    for p in src_root.rglob(basename):
        if not p.is_file():
            continue
        if best is None:
            best = p
            continue
        s = str(p)
        b = str(best)
        if "/src/backend/" in s and "/src/backend/" not in b:
            best = p
        elif len(s) < len(b):
            best = p
    return best


def _get_file_line_context(path: Path, line_1idx: int, window: int = 25) -> str:
    """Return a numbered context window around a 1-indexed line number."""
    if not path.exists():
        return f"// missing file: {path}"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        return f"// failed to read {path}: {e}"
    if not lines:
        return f"// empty file: {path}"
    i0 = max(1, int(line_1idx))
    start = max(1, i0 - window)
    end = min(len(lines), i0 + window)
    out = []
    for i in range(start, end + 1):
        out.append(f"{i}: {lines[i - 1]}")
    return "\n".join(out)
