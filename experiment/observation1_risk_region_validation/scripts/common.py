#!/usr/bin/env python3
"""Shared utilities for Observation 1 risk-region validation."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

csv.field_size_limit(sys.maxsize)


DBMS_ALIASES = {
    "mysql": "mysql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
}

SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inc", ".ic"}
TEST_MARKERS = (
    "/test/",
    "/tests/",
    "/mysql-test/",
    "/regress/",
    "/src/test/",
    "/unittest/",
    "/unit/",
)
DOC_MARKERS = ("/doc/", "/docs/", "/documentation/")
BUILD_MARKERS = ("/cmake/", "/ci/", "/.github/", "/build-aux/", "/packaging/")
GENERATED_MARKERS = ("/generated/", "/gen/", "/autom4te.cache/")


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def exp_dir() -> Path:
    return script_dir().parent


def sqleek_root_from_env(default: str = "/root/SQLeek") -> Path:
    return Path(os.environ.get("SQLEEK_ROOT", default)).resolve()


def canonical_dbms(dbms: str) -> str:
    key = dbms.lower()
    if key not in DBMS_ALIASES:
        raise ValueError(f"unsupported DBMS: {dbms}")
    return DBMS_ALIASES[key]


def stage1_dbms(dbms: str) -> str:
    dbms = canonical_dbms(dbms)
    return "postgres" if dbms == "postgresql" else dbms


def data_dir(dbms: str) -> Path:
    return exp_dir() / "data" / canonical_dbms(dbms)


def results_dir(dbms: str) -> Path:
    return exp_dir() / "results" / canonical_dbms(dbms)


def report_dir() -> Path:
    return exp_dir() / "reports"


def logs_dir() -> Path:
    return exp_dir() / "logs"


def ensure_tree() -> None:
    for path in [
        exp_dir() / "configs",
        data_dir("mysql"),
        data_dir("postgresql"),
        results_dir("mysql"),
        results_dir("postgresql"),
        exp_dir() / "results" / "combined",
        report_dir(),
        logs_dir(),
    ]:
        path.mkdir(parents=True, exist_ok=True)


def command_log_path() -> Path:
    ensure_tree()
    return logs_dir() / "commands.log"


def log_command(args: list[str] | tuple[str, ...], cwd: Path | None = None) -> None:
    line = shlex.join([str(a) for a in args])
    if cwd:
        line = f"(cd {shlex.quote(str(cwd))} && {line})"
    with command_log_path().open("a", encoding="utf-8") as fp:
        fp.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()} {line}\n")


def run(
    args: list[str] | tuple[str, ...],
    cwd: Path | None = None,
    check: bool = True,
    text: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    log_command(args, cwd)
    return subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = run(["git", "-C", str(repo), *args], check=check)
    return proc.stdout


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        return json.loads(text)
    data = yaml.safe_load(text)
    return data or {}


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def config_path(dbms: str) -> Path:
    return exp_dir() / "configs" / f"{canonical_dbms(dbms)}.yaml"


def load_config(dbms: str) -> dict[str, Any]:
    path = config_path(dbms)
    cfg = load_yaml(path)
    if not cfg:
        cfg = default_config(canonical_dbms(dbms))
    cfg["dbms"] = canonical_dbms(dbms)
    return cfg


def default_config(dbms: str) -> dict[str, Any]:
    dbms = canonical_dbms(dbms)
    root = sqleek_root_from_env()
    stage = stage1_dbms(dbms)
    src = root / "sources" / ("postgres" if dbms == "postgresql" else "mysql")
    return {
        "dbms": dbms,
        "sqleek_root": str(root),
        "source_repo": str(src),
        "source_path": str(src),
        "stage1_codeql_results_dir": str(
            root / "sqleek_pipeline" / "stage1_static" / "output" / "codeql_results" / stage
        ),
        "stage1_targets_dir": str(root / "sqleek_pipeline" / "stage1_static" / "output" / "targets"),
        "coverage": {
            "official_coverage_csv": "",
            "allow_fuzzing_coverage_for_main": False,
            "excluded_fuzzing_coverage_note": "Fuzzing/replay coverage is not used for Observation 1 main coverage-gap signal.",
        },
        "analysis": {
            "historical_repair_threshold": 2,
            "mapping_confidence_for_main": "high",
            "coverage_gap_primary": "below_dbms_median_branch_coverage",
        },
        "paths": {
            "data_dir": str(data_dir(dbms)),
            "results_dir": str(results_dir(dbms)),
            "reports_dir": str(report_dir()),
        },
    }


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized = list(rows)
    if fieldnames is None:
        keys: list[str] = []
        seen = set()
        for row in materialized:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        return list(csv.DictReader(fp))


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def stable_hash(text: str, n: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:n]


def stable_region_id(dbms: str, file_path: str, start_line: int, end_line: int, rule_id: str) -> str:
    raw = f"{canonical_dbms(dbms)}:{normalize_path(file_path)}:{start_line}:{end_line}:{rule_id}"
    return f"{canonical_dbms(dbms)}_{stable_hash(raw, 20)}"


def normalize_path(path: str) -> str:
    value = (path or "").replace("\\", "/").strip().strip('"')
    value = re.sub(r"^[A-Za-z]:", "", value)
    for prefix in ("/root/SQLeek/sources/mysql/", "/root/SQLeek/sources/postgres/", "/tmp/pg_src/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = re.sub(r"^/+", "", value)
    value = re.sub(r"/+", "/", value)
    return value


def component_from_path(path: str) -> str:
    path = normalize_path(path)
    parts = path.split("/")
    if not parts:
        return ""
    if parts[0] == "src" and len(parts) >= 3:
        return "/".join(parts[:3])
    if parts[0] in {"sql", "storage", "include", "libs"} and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def is_source_file(path: str) -> bool:
    return Path(normalize_path(path)).suffix.lower() in SOURCE_EXTS


def is_test_file(path: str) -> bool:
    p = "/" + normalize_path(path).lower()
    return any(marker in p for marker in TEST_MARKERS)


def is_doc_file(path: str) -> bool:
    p = "/" + normalize_path(path).lower()
    return any(marker in p for marker in DOC_MARKERS) or Path(p).suffix.lower() in {".md", ".rst", ".txt", ".sgml"}


def is_build_file(path: str) -> bool:
    p = "/" + normalize_path(path).lower()
    name = Path(p).name.lower()
    return any(marker in p for marker in BUILD_MARKERS) or name in {"cmakelists.txt", "makefile"} or name.endswith(".cmake")


def is_generated_file(path: str) -> bool:
    p = "/" + normalize_path(path).lower()
    name = Path(p).name.lower()
    return any(marker in p for marker in GENERATED_MARKERS) or name.endswith((".yy", ".tab.c", ".tab.h"))


def parse_iso_date(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return dt.datetime.fromisoformat(value)


def date_only(value: str) -> dt.date:
    return parse_iso_date(value).date()


def date_to_iso(value: dt.date | dt.datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    return value.isoformat()


def source_root(cfg: dict[str, Any]) -> Path:
    return Path(cfg.get("source_path") or cfg.get("source_repo")).resolve()


def find_source_file(cfg: dict[str, Any], file_path: str) -> Path | None:
    root = source_root(cfg)
    norm = normalize_path(file_path)
    candidates = [root / norm]
    if cfg["dbms"] == "postgresql" and not norm.startswith("src/"):
        candidates.append(root / "src" / norm)
    if cfg["dbms"] == "mysql" and norm.startswith("mysql/"):
        candidates.append(root / norm[len("mysql/") :])
    for cand in candidates:
        if cand.is_file():
            return cand
    # Last resort: basename search inside the repo, capped by first match.
    name = Path(norm).name
    if not name:
        return None
    try:
        proc = run(["git", "-C", str(root), "ls-files", f"*{name}"], check=False)
        for line in proc.stdout.splitlines():
            if line.endswith(name):
                cand = root / line
                if cand.is_file():
                    return cand
    except Exception:
        return None
    return None


CONTROL_WORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "catch",
    "foreach",
    "do",
}


FUNC_RE = re.compile(
    r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?\{?\s*$"
)


def enclosing_function_from_file(path: Path | None, line_no: int) -> tuple[str, int]:
    if path is None or not path.is_file() or line_no <= 0:
        return "", 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", 0
    idx = min(max(line_no - 1, 0), max(len(lines) - 1, 0))
    start_idx = -1
    name = ""
    window_start = max(0, idx - 500)
    pending = ""
    for i in range(idx, window_start - 1, -1):
        text = lines[i].strip()
        pending = (text + " " + pending).strip()
        if "{" not in pending and not text.endswith(")") and len(pending) < 400:
            continue
        candidate = pending.split("{", 1)[0].strip()
        m = FUNC_RE.search(candidate + "{")
        if m:
            found = m.group("name").split("::")[-1]
            if found not in CONTROL_WORDS:
                start_idx = i
                name = found
                break
        if len(pending) > 500:
            pending = text
    if not name:
        return "", 0
    depth = 0
    saw_open = False
    for j in range(start_idx, len(lines)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                saw_open = True
            elif ch == "}":
                depth -= 1
                if saw_open and depth <= 0:
                    return name, max(1, j - start_idx + 1)
    return name, max(1, len(lines) - start_idx)


def parse_codeql_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        reader = csv.reader(fp)
        for idx, row in enumerate(reader, start=1):
            if len(row) < 9:
                continue
            rows.append(
                {
                    "alert_name": row[0],
                    "description": row[1],
                    "severity": row[2],
                    "message": row[3],
                    "file_path": normalize_path(row[4]),
                    "start_line": int_or_zero(row[5]),
                    "start_col": int_or_zero(row[6]),
                    "end_line": int_or_zero(row[7]),
                    "end_col": int_or_zero(row[8]),
                    "csv_row": idx,
                    "csv_path": str(path),
                }
            )
    return rows


def int_or_zero(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


ISSUE_RE = re.compile(
    r"(?i)(?:bug|issue|ticket|bpo|mdev|bugdb|bug\s*#|pr)\s*[:# ]+\s*([A-Za-z]*-?\d{3,}|[0-9]{3,})"
)
CVE_RE = re.compile(r"(?i)\bCVE-\d{4}-\d{4,7}\b")
HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)


def extract_issue_id(message: str) -> str:
    ids = []
    for match in ISSUE_RE.finditer(message or ""):
        ids.append(match.group(1).upper())
    return ";".join(sorted(set(ids)))


def extract_cve_id(message: str) -> str:
    return ";".join(sorted(set(m.group(0).upper() for m in CVE_RE.finditer(message or ""))))


def extract_original_hash(message: str) -> str:
    text = message or ""
    if not re.search(r"(?i)(cherry|backport|backpatch|picked|original|commit)", text):
        return ""
    hashes = HASH_RE.findall(text)
    return hashes[0] if hashes else ""


def classify_bug_type(message: str) -> str:
    text = (message or "").lower()
    if any(k in text for k in ["cve", "security", "vulnerability"]):
        return "security"
    if any(k in text for k in ["crash", "segfault", "assert", "abort", "use-after-free", "buffer overflow", "memory corruption", "null pointer"]):
        return "crash"
    if any(k in text for k in ["incorrect result", "wrong result", "wrong answer", "miscompute", "wrongly"]):
        return "incorrect_result"
    if any(k in text for k in ["deadlock", "race"]):
        return "concurrency"
    if "leak" in text:
        return "memory_leak"
    return "bug_fix"


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def median(values: Iterable[float]) -> float:
    vals = [v for v in values if not math.isnan(v)]
    return statistics.median(vals) if vals else math.nan


def quantile(values: Iterable[float], q: float) -> float:
    vals = sorted(v for v in values if not math.isnan(v))
    if not vals:
        return math.nan
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def fisher_or_chi2(a: int, b: int, c: int, d: int) -> tuple[str, float]:
    try:
        from scipy.stats import chi2_contingency, fisher_exact  # type: ignore
    except Exception:
        return "unavailable", math.nan
    table = [[a, b], [c, d]]
    if min(a, b, c, d) < 5:
        return "fisher_exact", float(fisher_exact(table)[1])
    return "chi_square", float(chi2_contingency(table, correction=False)[1])


def odds_ratio_ci(a: int, b: int, c: int, d: int) -> tuple[float, float, float]:
    # Haldane-Anscombe correction avoids infinite intervals.
    aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    orv = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    lo = math.exp(math.log(orv) - 1.96 * se)
    hi = math.exp(math.log(orv) + 1.96 * se)
    return orv, lo, hi


def bh_adjust(pvalues: list[float]) -> list[float]:
    n = len(pvalues)
    indexed = sorted((p, i) for i, p in enumerate(pvalues))
    out = [math.nan] * n
    prev = 1.0
    for rank, (p, i) in enumerate(reversed(indexed), start=1):
        if math.isnan(p):
            out[i] = math.nan
            continue
        adj = min(prev, p * n / (n - rank + 1))
        prev = adj
        out[i] = adj
    return out


def write_markdown(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines.extend([f"## {heading}", "", body.rstrip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def sheet_name(name: str) -> str:
    invalid = "[]:*?/\\"
    clean = "".join("_" if ch in invalid else ch for ch in name)[:31]
    return clean or "Sheet"


def write_xlsx(path: Path, sheets: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Write a minimal Excel workbook using only the Python standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def col_name(idx: int) -> str:
        name = ""
        idx += 1
        while idx:
            idx, rem = divmod(idx - 1, 26)
            name = chr(65 + rem) + name
        return name

    def worksheet_xml(rows: list[dict[str, Any]], fields: list[str]) -> str:
        all_rows = [fields] + [[row.get(f, "") for f in fields] for row in rows]
        xml_rows = []
        for r_idx, row in enumerate(all_rows, start=1):
            cells = []
            for c_idx, value in enumerate(row):
                ref = f"{col_name(c_idx)}{r_idx}"
                if value is None:
                    cells.append(f'<c r="{ref}"/>')
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(float(value)):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    text = escape(str(value))
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
            xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
        )

    normalized = [(sheet_name(n), rows) for n, rows in sheets]
    workbook_sheets = []
    rels = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(normalized) + 1)
            )
            + "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        for i, (name, rows) in enumerate(normalized, start=1):
            fields: list[str] = []
            seen = set()
            for row in rows:
                for key in row.keys():
                    if key not in seen:
                        seen.add(key)
                        fields.append(key)
            if not fields:
                fields = ["note"]
            zf.writestr(f"xl/worksheets/sheet{i}.xml", worksheet_xml(rows, fields))
            workbook_sheets.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
