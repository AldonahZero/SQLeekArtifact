#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

SQLEEK_ROOT = Path(os.environ.get("SQLEEK_ROOT", "/root/SQLeek"))
if SQLEEK_ROOT.exists():
    sys.path.insert(0, str(SQLEEK_ROOT))

try:
    from config import (
        BUG_TYPES,
        DBMS_LIST,
        HIGH_THRESHOLD,
        LOW_THRESHOLD,
        OUTPUT_DIR,
        POLL_INTERVAL,
        REPLICATE_N,
        SCHEDULER_LOG,
        TARGETS_DIR,
    )
except Exception:
    BUG_TYPES = ["memory", "logic"]
    DBMS_LIST = ["mysql", "mariadb", "postgres", "monetdb"]
    HIGH_THRESHOLD = 0.4
    LOW_THRESHOLD = 0.03
    OUTPUT_DIR = Path("/workspace/output")
    POLL_INTERVAL = 60
    REPLICATE_N = 5
    SCHEDULER_LOG = Path("/workspace/logs/scheduler.log")
    TARGETS_DIR = Path("/workspace/targets")

TARGETS_DIR = Path(os.environ.get("TARGET_DIR", os.environ.get("SQLEEK_TARGET_DIR", str(TARGETS_DIR))))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", os.environ.get("SQLEEK_OUTPUT_DIR", str(OUTPUT_DIR))))
if "LOG_DIR" in os.environ and "SCHEDULER_LOG" not in os.environ and "SQLEEK_SCHEDULER_LOG" not in os.environ:
    SCHEDULER_LOG = Path(os.environ["LOG_DIR"]) / "scheduler.log"
else:
    SCHEDULER_LOG = Path(os.environ.get("SCHEDULER_LOG", os.environ.get("SQLEEK_SCHEDULER_LOG", str(SCHEDULER_LOG))))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", os.environ.get("SQLEEK_POLL_INTERVAL", str(POLL_INTERVAL))))
REPLICATE_N = int(os.environ.get("REPLICATE_N", os.environ.get("SQLEEK_REPLICATE_N", str(REPLICATE_N))))

RUNNING = True

# If a seed exhibits a "cursor -> DDL -> cursor execution" state machine, we
# want it to be prioritized even when bitmap/function proximity can't be
# computed (e.g., because our SQL seeds don't contain C function names).
SQL_STATE_BONUS_WEIGHT = 0.26

# Runtime overrides (CLI) — keep defaults in config.py unless explicitly changed.
LOW_THRESHOLD_RUNTIME = LOW_THRESHOLD
HIGH_THRESHOLD_RUNTIME = HIGH_THRESHOLD
SQL_STATE_BONUS_WEIGHT_RUNTIME = SQL_STATE_BONUS_WEIGHT
ENERGY_MIN_RUNTIME = 1
ENERGY_MAX_RUNTIME = int(os.environ.get("SQLEEK_ENERGY_MAX", "32"))
LOW_SCORE_ENERGY_RUNTIME = int(os.environ.get("SQLEEK_LOW_SCORE_ENERGY", "1"))
WRITE_ENERGY_RUNTIME = True

SUPPORTED_SEED_SUFFIXES = {".sql", ".test"}
SQL_LIKE_NUMERIC_SUFFIX = re.compile(r"\.\d+$")
TEMP_SEED_SUFFIXES = {".tmp", ".temp", ".swp", ".swo", ".bak"}
SQLRIGHT_MAX_FILE_BYTES = int(os.environ.get("SQLRIGHT_MAX_FILE_BYTES", str(1024 * 1024)))
MYSQLTEST_DIRECTIVES = {
    "append_file", "cat_file", "chmod", "connect", "connection", "copy_file",
    "dec", "delimiter", "diff_files", "disable_abort_on_error", "disable_info",
    "disable_metadata", "disable_parsing", "disable_ps_protocol", "disable_query_log",
    "disable_result_log", "disable_rpl_parse", "disable_warnings", "disconnect",
    "echo", "enable_abort_on_error", "enable_info", "enable_metadata",
    "enable_parsing", "enable_ps_protocol", "enable_query_log", "enable_result_log",
    "enable_rpl_parse", "enable_warnings", "error", "eval", "exec", "exit",
    "file_exists", "horizontal_results", "inc", "let", "list_files", "mkdir",
    "move_file", "perl", "query_get_value", "query_vertical", "real_sleep",
    "reap", "remove_file", "replace_column", "replace_regex", "replace_result",
    "require", "rmdir", "send", "send_eval", "shutdown_server", "skip", "sleep",
    "sorted_result", "source", "start_timer", "stop_timer", "system", "vertical_results",
    "write_file",
}


def stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def log(message: str) -> None:
    SCHEDULER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SCHEDULER_LOG.open("a", encoding="utf-8") as fp:
        fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    print(f"[seed_scheduler] {message}", flush=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_duration(value: str) -> int:
    text = str(value).strip().lower()
    if not text:
        return 0
    if text[-1:] in {"s", "m", "h", "d"}:
        amount = float(text[:-1])
        scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[text[-1]]
        return int(amount * scale)
    return int(float(text))


def apply_scheduler_config(path: str | None) -> None:
    """Apply optional JSON config mounted with SCHEDULER_CONFIG.

    Supported keys intentionally mirror runtime flags so experiments can be
    reproduced by snapshotting a single config file.
    """
    if not path:
        return
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit(f"scheduler config not found: {config_path}")
    data = load_json(config_path, {})
    if not isinstance(data, dict):
        raise SystemExit(f"scheduler config must be a JSON object: {config_path}")

    global LOW_THRESHOLD_RUNTIME, HIGH_THRESHOLD_RUNTIME
    global SQL_STATE_BONUS_WEIGHT_RUNTIME, ENERGY_MIN_RUNTIME, ENERGY_MAX_RUNTIME
    global LOW_SCORE_ENERGY_RUNTIME, WRITE_ENERGY_RUNTIME, POLL_INTERVAL, REPLICATE_N

    if "low_threshold" in data:
        LOW_THRESHOLD_RUNTIME = float(data["low_threshold"])
    if "high_threshold" in data:
        HIGH_THRESHOLD_RUNTIME = float(data["high_threshold"])
    if "state_bonus_weight" in data:
        SQL_STATE_BONUS_WEIGHT_RUNTIME = float(data["state_bonus_weight"])
    if "energy_min" in data:
        ENERGY_MIN_RUNTIME = max(0, int(data["energy_min"]))
    if "energy_max" in data:
        ENERGY_MAX_RUNTIME = max(ENERGY_MIN_RUNTIME, int(data["energy_max"]))
    if "low_score_energy" in data:
        LOW_SCORE_ENERGY_RUNTIME = max(0, int(data["low_score_energy"]))
    if "write_energy" in data:
        WRITE_ENERGY_RUNTIME = bool(data["write_energy"])
    if "poll_interval" in data:
        POLL_INTERVAL = max(1, int(data["poll_interval"]))
    if "replicate_n" in data:
        REPLICATE_N = max(0, int(data["replicate_n"]))


def load_target_lines(dbms: str, bug_type: str = "memory") -> list[str]:
    candidates = [
        TARGETS_DIR / f"{dbms}_{bug_type}.txt",
        TARGETS_DIR / f"{dbms}.targets",
        TARGETS_DIR / "targets.txt",
    ]
    for path in candidates:
        if not path.exists():
            continue
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return []


def load_distance_map(dbms: str) -> dict[str, float]:
    """Load optional Stage 1/2 distance data in common JSON or text layouts."""
    candidates = [
        TARGETS_DIR / f"{dbms}_distance.json",
        TARGETS_DIR / f"{dbms}_memory_distance.json",
        TARGETS_DIR / "distance.json",
        TARGETS_DIR / "distances.json",
        TARGETS_DIR / f"{dbms}_distance.txt",
        TARGETS_DIR / f"{dbms}_memory.distance.txt",
    ]
    out: dict[str, float] = {}
    for path in candidates:
        if not path.exists():
            continue
        if path.suffix == ".json":
            raw = load_json(path, {})
            if isinstance(raw, dict):
                raw = raw.get(dbms, raw.get("distances", raw))
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        try:
                            out[str(key)] = float(value)
                        except (TypeError, ValueError):
                            continue
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.replace(",", " ").split()
            if len(parts) < 2:
                continue
            try:
                out[str(parts[0])] = float(parts[1])
            except ValueError:
                continue
    return out


def target_tokens(dbms: str, bug_type: str = "memory") -> set[str]:
    tokens: set[str] = set()
    for line in load_target_lines(dbms, bug_type):
        file_part = line.split(":", 1)[0]
        stem = Path(file_part).stem
        for piece in stem.replace("-", "_").replace(".", "_").split("_"):
            if piece:
                tokens.add(piece)
                tokens.add(piece.lower())
        tokens.add(line)
        tokens.add(line.lower())
    return tokens


def normalized_callchains(raw: dict) -> dict[str, list[dict]]:
    """Load per-DBMS chain rows for proximity scoring.

    Expects top-level keys like ``postgres``, ``sqlite``, ``mysql`` (each a list of
    dicts with ``functions``, ``depth``, ``target`` / ``entry``). Extra metadata keys
    (``source``, ``chains``, ``by_entry``, …) are ignored.
    """
    chains: dict[str, list[dict]] = {dbms: [] for dbms in DBMS_LIST}
    for dbms, entries in raw.items():
        if dbms not in chains:
            continue
        for entry in entries:
            funcs = [str(f) for f in entry.get("functions", [])]
            if not funcs:
                funcs = [str(entry.get("entry", "")), str(entry.get("target", ""))]
            funcs = [f for f in funcs if f]
            if not funcs:
                continue
            depth = int(entry.get("depth") or max(1, len(funcs) - 1))
            chains[dbms].append({
                "functions": funcs,
                "depth": max(1, depth),
                "target": str(entry.get("target") or funcs[-1]),
            })
    return chains


def seed_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def is_hidden_or_temp_seed(path: Path) -> bool:
    """Return true for hidden editor/temp files that must not enter fuzzing."""
    for part in path.parts:
        if part in {".", ".."}:
            continue
        if part.startswith("."):
            return True
    name = path.name
    if name.endswith("~"):
        return True
    return path.suffix.lower() in TEMP_SEED_SUFFIXES


def is_supported_seed_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SEED_SUFFIXES or SQL_LIKE_NUMERIC_SUFFIX.search(path.name) is not None


def iter_seed_candidates(seed_dir: Path) -> list[Path]:
    """Deterministically scan recursive SQL seed candidates.

    The scanner deliberately ignores directories, symlinks, unsupported suffixes,
    and hidden temporary files. Empty files are returned so the caller can record
    an explicit rejection reason in manifests/logs.
    """
    candidates: list[Path] = []
    for path in seed_dir.rglob("*"):
        try:
            rel = path.relative_to(seed_dir)
        except ValueError:
            rel = path
        if is_hidden_or_temp_seed(rel):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if not is_supported_seed_path(path):
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda p: str(p.relative_to(seed_dir)).lower())


def sanitize_seed_component(value: str) -> str:
    safe = []
    for ch in value:
        if ch.isalnum() or ch in {"-", "_", ".", ":", ",", "=", "+"}:
            safe.append(ch)
        elif ch in {"/", "\\"}:
            safe.append("__")
        else:
            safe.append("_")
    text = "".join(safe).strip("._")
    return text or "seed"


def runtime_seed_priority(path: Path) -> str:
    """Stable SQLRight input ordering; the mounted corpus itself is unchanged."""
    name = path.name
    if name.startswith("sqleek_memory__"):
        return "00"
    if name.startswith("select_only__"):
        return "01"
    if name.startswith("official__"):
        return "02"
    return "03"


def normalized_seed_name(seed_dir: Path, path: Path, digest: str) -> str:
    rel = path.relative_to(seed_dir)
    rel_no_ext = rel.with_suffix("")
    return f"{runtime_seed_priority(path)}__{sanitize_seed_component(rel_no_ext.as_posix())}__{digest[:12]}.sql"


def copy_seed_unique(src: Path, dst_dir: Path, name: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / name
    if not target.exists():
        shutil.copy2(src, target)
        return target
    src_digest = hashlib.sha256(src.read_bytes()).hexdigest()
    try:
        if hashlib.sha256(target.read_bytes()).hexdigest() == src_digest:
            return target
    except OSError:
        pass
    stem = target.stem
    suffix = target.suffix or ".sql"
    idx = 1
    while True:
        candidate = dst_dir / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            shutil.copy2(src, candidate)
            return candidate
        idx += 1


def mysqltest_directive_reason(sql_text: str) -> str:
    """Detect mysqltest control directives that SQLRight should not receive."""
    for lineno, raw in enumerate(sql_text.splitlines(), start=1):
        line = raw.lstrip("\ufeff").strip()
        if not line:
            continue
        token = ""
        if line.startswith("--"):
            directive_text = line[2:].lstrip()
            if not directive_text:
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", directive_text)
            if match:
                token = match.group(1).lower()
        else:
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\b", line)
            if match:
                token = match.group(1).lower()
        if token in MYSQLTEST_DIRECTIVES:
            return f"mysqltest_directive:{token}:line:{lineno}"
        if re.match(r"^(if|while)\s*\(", line, re.IGNORECASE):
            return f"mysqltest_directive:{token}:line:{lineno}"
        if re.match(r"^(--)?\s*(source|error|let|echo|connect|connection|disconnect|send|reap|sleep)\b", line, re.IGNORECASE):
            shown = line[:40].replace("\t", " ")
            return f"mysqltest_directive:{shown}:line:{lineno}"
    return ""


def classify_mysql_seed_bytes(data: bytes, original_extension: str) -> tuple[str, str]:
    if not data:
        return "rejected", "empty_file"
    text = data.decode("utf-8", errors="ignore")
    # Griffin-compatible MySQL seeds commonly use NUL as a statement separator.
    # Preserve those bytes and do not treat NUL alone as proof of non-SQL data.
    normalized_text = text.replace("\x00", "\n")
    if not normalized_text.strip():
        return "rejected", "blank_sql"
    reason = mysqltest_directive_reason(normalized_text)
    if reason:
        return "deferred", reason
    return "kept", ""


def covered_tokens_from_sql(sql_text: str) -> set[str]:
    """Extract tokens from SQL text only (no bitmap/filename)."""
    tokens: set[str] = set()
    for part in sql_text.replace("(", " ").replace(")", " ").replace(",", " ").replace(";", " ").split():
        cleaned = "".join(ch for ch in part if ch.isalnum() or ch in "_:")
        if cleaned:
            tokens.add(cleaned)
            tokens.add(cleaned.lower())
    return tokens

def bitmap_tokens(path: Path) -> set[str]:
    tokens: set[str] = set()
    for candidate in (
        path.with_suffix(path.suffix + ".bitmap"),
        path.with_name(path.name + ".bitmap"),
        path.with_suffix(".bitmap"),
    ):
        if not candidate.exists():
            continue
        data = candidate.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        tokens.update(digest[i:i + 8] for i in range(0, len(digest), 8))
    return tokens


def covered_tokens(seed: Path) -> set[str]:
    text = seed_text(seed)
    tokens = set()
    for part in text.replace("(", " ").replace(")", " ").replace(",", " ").replace(";", " ").split():
        cleaned = "".join(ch for ch in part if ch.isalnum() or ch in "_:")
        if cleaned:
            tokens.add(cleaned)
            tokens.add(cleaned.lower())
    tokens.update(seed.name.replace(",", "_").replace(":", "_").split("_"))
    tokens.update(bitmap_tokens(seed))
    return tokens


def proximity_score(tokens: set[str], chains: list[dict]) -> float:
    score = 0.0
    lower_tokens = {token.lower() for token in tokens}
    for chain in chains:
        funcs = [str(f) for f in chain["functions"]]
        depth = max(1, int(chain["depth"]))
        matched = any(func in tokens or func.lower() in lower_tokens for func in funcs)
        if matched:
            score += 1.0 / depth
    return score


def queue_dirs() -> list[tuple[str, str, Path]]:
    dirs: list[tuple[str, str, Path]] = []
    for dbms in DBMS_LIST:
        for bug_type in BUG_TYPES:
            base = OUTPUT_DIR / f"{dbms}_{bug_type}" / "default" / "queue"
            if base.exists():
                dirs.append((dbms, bug_type, base))
        griffin_base = OUTPUT_DIR / f"{dbms}_memory" / "default" / "queue"
        if griffin_base.exists() and (dbms, "memory", griffin_base) not in dirs:
            dirs.append((dbms, "memory", griffin_base))
    return dirs


def load_weights() -> dict:
    default = {dbms: {bug_type: 1.0 for bug_type in BUG_TYPES} for dbms in DBMS_LIST}
    runtime_path = OUTPUT_DIR / ".scheduler" / "weights.json"
    data = load_json(runtime_path, None)
    if data is None:
        data = load_json(TARGETS_DIR / "weights.json", default)
    for dbms in DBMS_LIST:
        data.setdefault(dbms, {})
        for bug_type in BUG_TYPES:
            data[dbms].setdefault(bug_type, 1.0)
    return data


def save_runtime_weights(weights: dict) -> Path:
    path = OUTPUT_DIR / ".scheduler" / "weights.json"
    save_json(path, weights)
    return path


def load_phi_mapping() -> dict[str, list[str]]:
    """Load function->clause tags mapping (generated in stage1).

    Missing file is treated as an empty mapping.
    """
    path = TARGETS_DIR / "phi_mapping.json"
    if not path.exists():
        return {}
    try:
        raw = load_json(path, {})
    except Exception:
        return {}
    phi = raw.get("phi_mapping") if isinstance(raw, dict) else None
    if isinstance(phi, dict):
        # normalize values to list[str]
        out: dict[str, list[str]] = {}
        for k, v in phi.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v if str(x).strip()]
        return out
    return {}


def seed_sql_clauses(sql_text: str) -> set[str]:
    """Heuristic SQL-clause detector.

    Returns a set of clause tags that should align with phi_mapping.json's
    UPPER_SNAKE_CASE tags.
    """
    upper = sql_text.upper()
    clauses: set[str] = set()

    if "CREATE TYPE" in upper:
        clauses.add("CREATE_TYPE")
    if "ALTER TYPE" in upper:
        clauses.add("ALTER_TYPE")
    if "DECLARE" in upper and "CURSOR" in upper:
        clauses.add("CURSOR")
    if "FETCH" in upper:
        clauses.add("FETCH")
    if "ROW(" in upper:
        clauses.add("ROW_CONSTRUCTOR")

    if "PREPARE" in upper:
        clauses.add("PREPARE")
    if "EXECUTE" in upper:
        clauses.add("EXECUTE_PREPARED")

    if "::" in sql_text or "CAST(" in upper:
        clauses.add("CAST")
        clauses.add("TYPE_COERCION")
    if "::TEXT" in upper or "::TEXT)" in upper or " AS TEXT" in upper:
        clauses.add("CAST_TO_TEXT")
        clauses.add("TEXT_OUTPUT")

    if "DECLARE" in upper and "CURSOR" in upper and "FETCH" in upper:
        clauses.add("CURSOR_FETCH")

    # Triggers show up in phi_mapping as FETCH/SELECT/etc usually; we keep it
    # lightweight here.
    if "CREATE TRIGGER" in upper or "TRIGGER" in upper:
        clauses.add("TRIGGER")

    # Some phi tags correspond to broad categories.
    if "BEGIN;" in upper or upper.startswith("BEGIN") or " BEGIN " in upper:
        clauses.add("BEGIN")
    if "COMMIT" in upper:
        clauses.add("COMMIT")
    if any(k in upper for k in ("ALTER TABLE", "DROP ", "RENAME ")):
        clauses.add("DDL")

    return clauses


def replicate(seed: Path, score: float) -> None:
    digest = hashlib.sha1(seed.read_bytes()).hexdigest()[:12]
    for idx in range(REPLICATE_N):
        target = seed.parent / f"hi_val_score_{score:.3f}_{idx}_{digest}_{seed.name}"
        if not target.exists():
            shutil.copy2(seed, target)


def defer(seed: Path, dbms: str, bug_type: str) -> None:
    target_dir = OUTPUT_DIR / ".deferred" / dbms / bug_type
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / seed.name
    if target.exists():
        target = target_dir / f"{int(time.time())}_{seed.name}"
    # IMPORTANT: Do NOT remove/move queue entries during AFL++ dry run.
    # We only copy into .deferred/ for bookkeeping; the original queue file stays.
    shutil.copy2(seed, target)


def replicate_into_queue(seed: Path, queue_dir: Path, score: float, reason: str, copies: int | None = None) -> int:
    digest = hashlib.sha1(seed.read_bytes()).hexdigest()[:12]
    made = 0
    for idx in range(REPLICATE_N if copies is None else max(0, copies)):
        target = queue_dir / f"hi_val_{reason}_score_{score:.3f}_{idx}_{digest}_{seed.name}"
        if not target.exists():
            shutil.copy2(seed, target)
            made += 1
    return made


def defer_in_queue(seed: Path, queue_dir: Path) -> None:
    deferred = queue_dir.parent / ".deferred"
    deferred.mkdir(parents=True, exist_ok=True)
    target = deferred / seed.name
    if target.exists():
        target = deferred / f"{int(time.time())}_{seed.name}"
    # IMPORTANT: Keep queue/ stable; only copy to .deferred/.
    shutil.copy2(seed, target)


def online_skip_list_path(dbms: str) -> Path:
    """Host path visible in-container as /fuzz_output/.deferred/skip_list.txt."""
    return OUTPUT_DIR / f"{dbms}_memory" / ".deferred" / "skip_list.txt"


def mark_skip(dbms: str, seed_name: str) -> None:
    path = online_skip_list_path(dbms)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(seed_name + "\n")


def generated_state_sql(dbms: str) -> str:
    """Generate a seed that pushes multi-statement state toward deeper targets."""
    if dbms == "postgres":
        return """-- SQLeek generated state seed: postgres
CREATE TYPE sqleek_t AS (a INT, b INT);
BEGIN;
DECLARE sqleek_c CURSOR FOR SELECT (1, 2)::sqleek_t;
FETCH sqleek_c;
ALTER TYPE sqleek_t ALTER ATTRIBUTE b TYPE TEXT;
FETCH sqleek_c;
COMMIT;
"""
    if dbms in {"mysql", "mariadb"}:
        return """-- SQLeek generated state seed: mysql-compatible
CREATE DATABASE IF NOT EXISTS sqleek_state;
USE sqleek_state;
DROP TABLE IF EXISTS sqleek_t;
CREATE TABLE sqleek_t (id INT PRIMARY KEY, a INT, b VARCHAR(32));
INSERT INTO sqleek_t VALUES (1, 7, 'x'), (2, 11, 'y');
PREPARE sqleek_stmt FROM 'SELECT id, CAST(a AS CHAR), b FROM sqleek_t WHERE a >= ? ORDER BY id';
SET @sqleek_v = 1;
EXECUTE sqleek_stmt USING @sqleek_v;
ALTER TABLE sqleek_t ADD COLUMN c TEXT;
UPDATE sqleek_t SET c = CONCAT(b, a);
EXECUTE sqleek_stmt USING @sqleek_v;
DEALLOCATE PREPARE sqleek_stmt;
"""
    if dbms == "monetdb":
        return """-- SQLeek generated state seed: monetdb
DROP TABLE IF EXISTS sqleek_t;
CREATE TABLE sqleek_t (id INT, a INT, b STRING);
INSERT INTO sqleek_t VALUES (1, 7, 'x'), (2, 11, 'y');
SELECT id, CAST(a AS STRING), b FROM sqleek_t WHERE a >= 1 ORDER BY id;
ALTER TABLE sqleek_t ADD COLUMN c STRING;
UPDATE sqleek_t SET c = b || CAST(a AS STRING);
SELECT id, c FROM sqleek_t WHERE c IS NOT NULL ORDER BY id;
"""
    return """-- SQLeek generated state seed: generic
CREATE TABLE sqleek_t (id INT, a INT);
INSERT INTO sqleek_t VALUES (1, 7);
SELECT * FROM sqleek_t;
ALTER TABLE sqleek_t ADD COLUMN b INT;
SELECT * FROM sqleek_t;
"""


def inject_combined_seed(queue_dir: Path, dbms: str) -> Path | None:
    """Inject a combined seed that enforces a DBMS-appropriate state sequence.

    This is a last-resort injection to avoid relying purely on mutation luck.
    """
    combined_path = queue_dir / "injected_combined_000.sql"
    if combined_path.exists():
        return combined_path

    combined_path.write_text(generated_state_sql(dbms), encoding="utf-8")
    return combined_path


def cursor_ddl_fetch_conditions(sql_upper: str) -> dict[str, bool]:
    """Generic state conditions for deep multi-statement DBMS execution.

    PostgreSQL gets a cursor-DDL-fetch sequence; MySQL/MariaDB/MonetDB get a
    prepare/select-DDL-execute/select sequence. The scheduler rewards state
    progress without hard-coding a specific bug trigger.
    """
    # Basic presence checks
    has_create_type = "CREATE TYPE" in sql_upper
    has_cursor = ("DECLARE" in sql_upper and "CURSOR" in sql_upper)
    fetch_positions = [i for i in range(len(sql_upper)) if sql_upper.startswith("FETCH", i)]
    has_first_fetch = len(fetch_positions) >= 1
    has_alter_type = "ALTER TYPE" in sql_upper

    # Order-aware second fetch: must have ALTER TYPE after cursor, and a FETCH after that ALTER.
    cursor_pos = sql_upper.find("CURSOR")
    alter_pos = -1
    if cursor_pos != -1:
        # only consider ALTER TYPE after CURSOR declaration
        after_cursor = sql_upper[cursor_pos:]
        rel = after_cursor.find("ALTER TYPE")
        if rel != -1:
            alter_pos = cursor_pos + rel

    has_second_fetch = False
    if alter_pos != -1:
        for fp in fetch_positions:
            if fp > alter_pos:
                has_second_fetch = True
                break

    execute_positions = [i for i in range(len(sql_upper)) if sql_upper.startswith("EXECUTE", i)]
    select_positions = [i for i in range(len(sql_upper)) if sql_upper.startswith("SELECT", i)]
    ddl_positions = [
        pos for pos in (
            sql_upper.find("ALTER TABLE"),
            sql_upper.find("ALTER TYPE"),
            sql_upper.find("CREATE TABLE"),
            sql_upper.find("DROP TABLE"),
        )
        if pos >= 0
    ]
    first_state_exec = execute_positions[0] if execute_positions else (select_positions[0] if select_positions else -1)
    first_ddl_after_exec = -1
    if first_state_exec >= 0:
        after = [pos for pos in ddl_positions if pos > first_state_exec]
        first_ddl_after_exec = min(after) if after else -1
    exec_after_ddl = False
    if first_ddl_after_exec >= 0:
        exec_after_ddl = any(pos > first_ddl_after_exec for pos in execute_positions + select_positions)

    return {
        "CREATE_TYPE": has_create_type,
        "CURSOR": has_cursor,
        "FIRST_FETCH": has_first_fetch,
        "ALTER_TYPE": has_alter_type,
        "SECOND_FETCH": has_second_fetch,
        "PREPARE": "PREPARE" in sql_upper,
        "STATE_EXEC_BEFORE_DDL": first_state_exec >= 0,
        "DDL_AFTER_EXEC": first_ddl_after_exec >= 0,
        "STATE_EXEC_AFTER_DDL": exec_after_ddl,
    }


def state_sequence_bonus(conds: dict[str, bool]) -> float:
    """Convert state-condition hits into an additive bonus.

    Important: returns 0 for weak/partial sequences so low-value seeds can
    still be deferred.
    """
    n = sum(1 for v in conds.values() if v)
    # Only start rewarding once we have enough evidence of a state sequence.
    # This keeps weak/partial seeds deferable.
    if n < 3:
        return 0.0
    # each additional condition beyond 2 doubles the bonus
    return 2.0 ** max(0, n - 2)


def phi_clause_proximity_score(
    seed_clauses: set[str],
    chains: list[dict],
    phi_mapping: dict[str, list[str]],
) -> float:
    """Compute proximity based on clause tags derived from phi_mapping.

    For each chain, we look at clause tags mapped from its functions
    (via phi_mapping), and award 1/depth if the seed SQL covers any of those
    clause tags.
    """
    if not seed_clauses or not chains:
        return 0.0

    score = 0.0
    for chain in chains:
        depth = max(1, int(chain.get("depth") or 1))
        funcs = [str(f) for f in chain.get("functions") or []]
        chain_tags: set[str] = set()
        for fn in funcs:
            for tag in phi_mapping.get(fn, []):
                t = str(tag).strip().upper()
                if not t or t == "UNKNOWN":
                    continue
                chain_tags.add(t)
        if not chain_tags:
            continue
        if chain_tags.intersection(seed_clauses):
            score += 1.0 / depth
    # Normalize by number of chains to keep score in a stable range across
    # different target sets/callchains.json sizes.
    return score / max(1, len(chains))


def target_distance_score(tokens: set[str], dbms: str, bug_type: str = "memory") -> float:
    """Small additive signal from target/distance artifacts.

    Stage 1 target files are source locations, while SQL seeds rarely contain C
    function names. This score is intentionally bounded and secondary; it lets
    hand-authored or generated seeds that name a component/function get a
    little priority without drowning out phi/state evidence.
    """
    t_tokens = target_tokens(dbms, bug_type)
    if not t_tokens:
        return 0.0

    lower = {t.lower() for t in tokens}
    matches = {tok for tok in t_tokens if tok in tokens or tok.lower() in lower}
    if not matches:
        return 0.0

    distance = load_distance_map(dbms)
    if not distance:
        return min(0.2, len(matches) / max(1, len(t_tokens)))

    weighted = 0.0
    for match in matches:
        # Smaller distance means closer to target. Unknown entries receive the
        # weakest positive value.
        d = distance.get(match, distance.get(match.lower(), 1000.0))
        weighted += 1.0 / (1.0 + max(0.0, float(d)))
    return min(0.3, weighted / max(1, len(t_tokens)))


def score_to_energy(score: float) -> int:
    if score < LOW_THRESHOLD_RUNTIME:
        return LOW_SCORE_ENERGY_RUNTIME
    if score >= HIGH_THRESHOLD_RUNTIME:
        return ENERGY_MAX_RUNTIME
    span = max(0.000001, HIGH_THRESHOLD_RUNTIME - LOW_THRESHOLD_RUNTIME)
    ratio = (score - LOW_THRESHOLD_RUNTIME) / span
    return max(ENERGY_MIN_RUNTIME, int(round(ENERGY_MIN_RUNTIME + ratio * (ENERGY_MAX_RUNTIME - ENERGY_MIN_RUNTIME))))


def energy_file_path(dbms: str) -> Path:
    return OUTPUT_DIR / f"{dbms}_memory" / ".deferred" / "energy.tsv"


def write_energy_line(fp, seed_name: str, score: float) -> int:
    energy = score_to_energy(score)
    fp.write(f"{seed_name}\t{score:.6f}\t{energy}\n")
    return energy


def write_energy(dbms: str, seed_name: str, score: float) -> int:
    if not WRITE_ENERGY_RUNTIME:
        return score_to_energy(score)
    path = energy_file_path(dbms)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        return write_energy_line(fp, seed_name, score)


def process_online_seeds(
    dbms: str,
    queue_dir: Path,
    chains: dict[str, list[dict]],
    seen: set[str],
    phi_mapping: dict[str, list[str]],
    enable_bonus: bool = True,
) -> tuple[int, int]:
    """Return (new_seen_count, full_conds_hits_in_new)."""
    weights = load_weights()
    changed_weights = False
    new_seen = 0
    full_hits = 0

    for seed in sorted(queue_dir.glob("id:*")):
        if not seed.is_file() or seed.name.startswith(".") or seed.name.startswith("hi_val_"):
            continue
        if seed.name in seen:
            continue
        seen.add(seed.name)
        new_seen += 1

        text = seed_text(seed)
        upper = text.upper()
        conds = cursor_ddl_fetch_conditions(upper)
        nconds = sum(1 for v in conds.values() if v)
        if nconds == len(conds):
            full_hits += 1

        seed_clauses = seed_sql_clauses(text)
        base_fn = proximity_score(covered_tokens(seed), chains.get(dbms, []))
        base_phi = phi_clause_proximity_score(seed_clauses, chains.get(dbms, []), phi_mapping)
        base_target = target_distance_score(covered_tokens(seed), dbms, bug_type="memory")
        state_bonus = state_sequence_bonus(conds) if enable_bonus else 0.0
        final = base_fn + base_phi + base_target + SQL_STATE_BONUS_WEIGHT_RUNTIME * state_bonus
        assigned_energy = write_energy(dbms, seed.name, final)

        log(
            f"{dbms}/memory: {seed.name} base_fn={base_fn:.4f} base_phi={base_phi:.4f} "
            f"base_target={base_target:.4f} state_bonus={state_bonus:.2f} "
            f"final={final:.4f} assigned_energy={assigned_energy} conds={nconds}/{len(conds)}"
        )

        if final > HIGH_THRESHOLD_RUNTIME:
            weights[dbms]["memory"] = round(float(weights[dbms]["memory"]) + final, 6)
            changed_weights = True
            log(f"{dbms}/memory: scheduled high-value seed {seed.name} via energy_file assigned_energy={assigned_energy}")
        elif final >= LOW_THRESHOLD_RUNTIME and assigned_energy > ENERGY_MIN_RUNTIME:
            log(f"{dbms}/memory: scheduled medium-value seed {seed.name} via energy_file assigned_energy={assigned_energy}")
        elif final < LOW_THRESHOLD_RUNTIME:
            try:
                mark_skip(dbms, seed.name)
                log(f"{dbms}/memory: scheduled low-value seed {seed.name} assigned_energy={assigned_energy}")
            except OSError as exc:
                log(f"{dbms}/memory: failed to mark skip for {seed.name}: {exc}")

    if changed_weights:
        path = save_runtime_weights(weights)
        log(f"updated runtime weights {path}")

    return new_seen, full_hits


def run_online_scheduler(dbms: str, duration_s: int | None, inject_after_cycles: int) -> None:
    raw_chains = load_json(TARGETS_DIR / "callchains.json", {})
    chains = normalized_callchains(raw_chains)
    phi_mapping = load_phi_mapping()

    queue_dir = OUTPUT_DIR / f"{dbms}_memory" / "default" / "queue"
    seen: set[str] = set()
    cycle = 0
    injected = False
    start = time.time()

    log(f"online scheduler started dbms={dbms} poll={POLL_INTERVAL}s queue={queue_dir}")

    while RUNNING:
        if duration_s is not None and time.time() - start >= duration_s:
            log("online scheduler duration reached; stopping")
            break

        cycle += 1
        if not queue_dir.exists():
            log(f"[cycle {cycle}] queue not ready: {queue_dir}")
            time.sleep(POLL_INTERVAL)
            continue

        new_seen, full_hits = process_online_seeds(
            dbms, queue_dir, chains, seen, phi_mapping, enable_bonus=True
        )
        if new_seen:
            log(f"[cycle {cycle}] processed new seeds: {new_seen} (full_conds_hits={full_hits})")
        else:
            log(f"[cycle {cycle}] no new seeds")

        # No direct queue injection here: the closed loop feeds AFL through
        # energy.tsv, and AFL applies it when the queue entry is actually fuzzed.
        if not injected and cycle >= inject_after_cycles:
            injected = True
            log(f"[cycle {cycle}] direct queue injection disabled; scheduler feedback is energy_file")

        time.sleep(POLL_INTERVAL)

    log("online scheduler stopped")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SQLeek Stage 3 seed scheduler")
    p.add_argument("--mode", choices=["offline", "online", "prefilter", "energy-plan"], default="offline")
    p.add_argument("--dbms", choices=DBMS_LIST, default="postgres")
    p.add_argument("--duration", type=int, default=0, help="seconds; 0 means run until interrupted")
    p.add_argument("--duration-text", type=str, default="", help="duration with suffix, e.g. 60s, 24h")
    p.add_argument("--inject-after-cycles", type=int, default=5, help="inject combined seed after N poll cycles")
    p.add_argument("--seed-dir", type=str, default="", help="prefilter: input seeds directory")
    p.add_argument("--out-keep-dir", type=str, default="", help="prefilter: output keep directory")
    p.add_argument("--out-deferred-dir", type=str, default="", help="prefilter: output deferred directory")
    p.add_argument("--config", type=str, default=os.environ.get("SCHEDULER_CONFIG", ""), help="scheduler JSON config")
    p.add_argument("--target-dir", type=str, default="", help="override target artifact directory")
    p.add_argument("--output-dir", type=str, default="", help="override scheduler/fuzzer output directory")
    p.add_argument("--log-file", type=str, default="", help="override scheduler log file")
    p.add_argument("--poll-interval", type=int, default=None, help="override scheduler poll interval")
    p.add_argument("--replicate-n", type=int, default=None, help="override high-score replication count")
    p.add_argument("--low-threshold", type=float, default=None, help="override LOW_THRESHOLD (default from config.py)")
    p.add_argument("--high-threshold", type=float, default=None, help="override HIGH_THRESHOLD (default from config.py)")
    p.add_argument("--state-bonus-weight", type=float, default=None, help="override SQL_STATE_BONUS_WEIGHT (default 0.26)")
    p.add_argument("--energy-max", type=int, default=None, help="max mutation energy written to energy.tsv")
    return p.parse_args()


def process_once(chains: dict[str, list[dict]], seen: set[str]) -> None:
    weights = load_weights()
    changed_weights = False
    phi_mapping = load_phi_mapping()

    for dbms, bug_type, qdir in queue_dirs():
        for seed in sorted(qdir.iterdir()):
            if not seed.is_file() or seed.name.startswith("."):
                continue
            key = str(seed.resolve())
            if key in seen or seed.name.startswith("hi_val_"):
                continue
            seen.add(key)

            text = seed_text(seed)
            upper = text.upper()
            conds = cursor_ddl_fetch_conditions(upper)
            state_bonus = state_sequence_bonus(conds)
            seed_clauses = seed_sql_clauses(text)

            base_fn = proximity_score(covered_tokens(seed), chains.get(dbms, []))
            base_phi = phi_clause_proximity_score(seed_clauses, chains.get(dbms, []), phi_mapping)
            base_target = target_distance_score(covered_tokens(seed), dbms, bug_type=bug_type)

            score = base_fn + base_phi + base_target + SQL_STATE_BONUS_WEIGHT_RUNTIME * state_bonus
            write_energy(dbms, seed.name, score)
            log(
                f"{dbms}/{bug_type}: {seed.name} score={score:.4f} "
                f"base_fn={base_fn:.4f} base_phi={base_phi:.4f} "
                f"base_target={base_target:.4f} state_bonus={state_bonus:.2f} "
                f"energy={score_to_energy(score)}"
            )

            if score > HIGH_THRESHOLD_RUNTIME:
                replicate(seed, score)
                weights[dbms][bug_type] = round(float(weights[dbms][bug_type]) + score, 6)
                changed_weights = True
                log(f"{dbms}/{bug_type}: replicated high-value seed {seed.name}")
            elif score < LOW_THRESHOLD_RUNTIME:
                try:
                    defer(seed, dbms, bug_type)
                    log(f"{dbms}/{bug_type}: deferred low-value seed {seed.name}")
                except OSError as exc:
                    log(f"{dbms}/{bug_type}: failed to defer {seed.name}: {exc}")

    if changed_weights:
        path = save_runtime_weights(weights)
        log(f"updated runtime weights {path}")


def score_sql(sql_text: str, dbms: str, chains: dict[str, list[dict]], phi_mapping: dict[str, list[str]]) -> float:
    """Compute the same final score used by the online scheduler for a SQL string."""
    upper = sql_text.upper()
    conds = cursor_ddl_fetch_conditions(upper)
    seed_clauses = seed_sql_clauses(sql_text)

    base_fn = proximity_score(covered_tokens_from_sql(sql_text), chains.get(dbms, []))
    base_phi = phi_clause_proximity_score(seed_clauses, chains.get(dbms, []), phi_mapping)
    base_target = target_distance_score(covered_tokens_from_sql(sql_text), dbms, bug_type="memory")
    state_bonus = state_sequence_bonus(conds)
    return base_fn + base_phi + base_target + SQL_STATE_BONUS_WEIGHT_RUNTIME * state_bonus


def run_energy_plan(dbms: str, seed_dir: Path) -> Path:
    """Write deterministic SQLeek energy assignments for AFL input seeds.

    This file is consumed by the SQLRight AFL patch. It is written before AFL
    starts so initial seeds are scheduled by energy rather than copied into the
    output queue after AFL has already built its in-memory queue.
    """
    raw_chains = load_json(TARGETS_DIR / "callchains.json", {})
    chains = normalized_callchains(raw_chains)
    phi_mapping = load_phi_mapping()
    path = energy_file_path(dbms)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    count = 0
    total_energy = 0
    with tmp.open("w", encoding="utf-8") as fp:
        fp.write("# seed_name\tscore\tassigned_energy\n")
        for seed in sorted(seed_dir.iterdir(), key=lambda x: x.name.lower()):
            if not seed.is_file() or seed.name.startswith(".") or seed.stat().st_size <= 0:
                continue
            text = seed_text(seed)
            final = score_sql(text, dbms, chains, phi_mapping)
            energy = write_energy_line(fp, seed.name, final)
            count += 1
            total_energy += energy
            log(f"energy_plan {dbms}/memory: {seed.name} final={final:.4f} assigned_energy={energy}")
    os.replace(tmp, path)
    log(f"energy_plan complete dbms={dbms} seeds={count} total_assigned_energy={total_energy} path={path}")
    return path


def run_prefilter(dbms: str, seed_dir: Path, out_keep_dir: Path, out_deferred_dir: Path) -> int:
    """Prefilter Stage2 seeds before starting AFL++.

    We decide *before* fuzzing which seeds should be in the initial corpus,
    so AFL++ dry run never races with a scheduler that mutates queue/.

    This is a compatibility prefilter only: it rejects empty/binary seeds and
    defers mysqltest control scripts that SQLRight cannot safely execute as raw
    SQL. Directed scoring and energy assignment remain in the online scheduler.
    """
    out_keep_dir.mkdir(parents=True, exist_ok=True)
    out_deferred_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir = out_deferred_dir.parent / "rejected_initial"
    rejected_dir.mkdir(parents=True, exist_ok=True)

    kept = 0
    deferred = 0
    rejected = 0
    total = 0
    extension_counts = {".sql": 0, ".test": 0}
    for p in iter_seed_candidates(seed_dir):
        total += 1
        ext = p.suffix.lower()
        extension_counts[ext] = extension_counts.get(ext, 0) + 1
        try:
            data = p.read_bytes()
        except OSError as exc:
            rejected += 1
            log(f"prefilter rejected unreadable seed {p}: {exc}")
            continue
        digest = hashlib.sha256(data).hexdigest()
        name = normalized_seed_name(seed_dir, p, digest)
        if len(data) > SQLRIGHT_MAX_FILE_BYTES:
            copy_seed_unique(p, out_deferred_dir, name)
            deferred += 1
            log(
                f"prefilter deferred {p.relative_to(seed_dir)} "
                f"reason=sqlright_max_file_exceeded:size:{len(data)}:limit:{SQLRIGHT_MAX_FILE_BYTES}"
            )
            continue
        status, reason = classify_mysql_seed_bytes(data, ext)
        if status == "kept":
            copy_seed_unique(p, out_keep_dir, name)
            kept += 1
        elif status == "deferred":
            copy_seed_unique(p, out_deferred_dir, name)
            deferred += 1
            log(f"prefilter deferred {p.relative_to(seed_dir)} reason={reason}")
        else:
            copy_seed_unique(p, rejected_dir, name)
            rejected += 1
            log(f"prefilter rejected {p.relative_to(seed_dir)} reason={reason}")

    log(
        f"prefilter dbms={dbms} seeds_in={total} kept={kept} deferred={deferred} rejected={rejected} "
        f"sql={extension_counts.get('.sql', 0)} test={extension_counts.get('.test', 0)} "
        f"keep_dir={out_keep_dir} deferred_dir={out_deferred_dir} rejected_dir={rejected_dir}"
    )
    return kept

def main() -> None:
    args = parse_args()
    global LOW_THRESHOLD_RUNTIME, HIGH_THRESHOLD_RUNTIME, SQL_STATE_BONUS_WEIGHT_RUNTIME
    global TARGETS_DIR, OUTPUT_DIR, SCHEDULER_LOG, POLL_INTERVAL, REPLICATE_N, ENERGY_MAX_RUNTIME

    if args.target_dir:
        TARGETS_DIR = Path(args.target_dir)
    if args.output_dir:
        OUTPUT_DIR = Path(args.output_dir)
    if args.log_file:
        SCHEDULER_LOG = Path(args.log_file)
    apply_scheduler_config(args.config)
    if args.poll_interval is not None:
        POLL_INTERVAL = max(1, int(args.poll_interval))
    if args.replicate_n is not None:
        REPLICATE_N = max(0, int(args.replicate_n))
    if args.low_threshold is not None:
        LOW_THRESHOLD_RUNTIME = float(args.low_threshold)
    if args.high_threshold is not None:
        HIGH_THRESHOLD_RUNTIME = float(args.high_threshold)
    if args.state_bonus_weight is not None:
        SQL_STATE_BONUS_WEIGHT_RUNTIME = float(args.state_bonus_weight)
    if args.energy_max is not None:
        ENERGY_MAX_RUNTIME = max(ENERGY_MIN_RUNTIME, int(args.energy_max))

    if args.mode == "online":
        duration_value = parse_duration(args.duration_text) if args.duration_text else args.duration
        duration_s = None if duration_value <= 0 else int(duration_value)
        run_online_scheduler(args.dbms, duration_s=duration_s, inject_after_cycles=int(args.inject_after_cycles))
        return
    if args.mode == "prefilter":
        if not args.seed_dir or not args.out_keep_dir or not args.out_deferred_dir:
            raise SystemExit("prefilter mode requires --seed-dir --out-keep-dir --out-deferred-dir")
        kept = run_prefilter(
            args.dbms,
            seed_dir=Path(args.seed_dir),
            out_keep_dir=Path(args.out_keep_dir),
            out_deferred_dir=Path(args.out_deferred_dir),
        )
        if kept <= 0:
            raise SystemExit("prefilter produced empty keep set; refusing to proceed")
        return
    if args.mode == "energy-plan":
        if not args.seed_dir:
            raise SystemExit("energy-plan mode requires --seed-dir")
        run_energy_plan(args.dbms, Path(args.seed_dir))
        return

    raw_chains = load_json(TARGETS_DIR / "callchains.json", {})
    chains = normalized_callchains(raw_chains)
    seen: set[str] = set()
    log("scheduler started (offline)")

    while RUNNING:
        process_once(chains, seen)
        for _ in range(POLL_INTERVAL):
            if not RUNNING:
                break
            time.sleep(1)

    log("scheduler stopped (offline)")


if __name__ == "__main__":
    main()
