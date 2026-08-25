"""Execution and repair of concrete Stage 2 SQL candidates.

The validator deliberately operates after template expansion.  Every concrete
candidate is executed through a DBMS-level command configuration, and a failed
execution is passed to the LLM repair prompt together with diagnostics and any
configured coverage output.  Only a candidate whose final execution succeeds
is handed back to the seed writer.

Commands are lists (or shell-like strings in the JSON configuration) and are
run without a shell.  The SQL candidate is sent on stdin, so the defaults only
name client executables (``psql``, ``mysql``, ...); machine-specific paths and
database options belong in ``SQLEEK_STAGE2_EXECUTOR_CONFIG`` or the JSON value
in ``SQLEEK_STAGE2_EXECUTORS``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


MAX_REPAIR_ROUNDS = 3
_TAIL_LIMIT = 4000


DEFAULT_EXECUTORS: dict[str, dict[str, Any]] = {
    "postgresql": {"command": ["psql", "--set", "ON_ERROR_STOP=1"]},
    "postgres": {"command": ["psql", "--set", "ON_ERROR_STOP=1"]},
    "mysql": {"command": ["mysql", "--batch", "--raw"]},
    "mariadb": {"command": ["mariadb", "--batch", "--raw"]},
    "sqlite": {"command": ["sqlite3"]},
    "monetdb": {"command": ["mclient"]},
}


@dataclass
class ExecutorConfig:
    """DBMS-level command configuration used by the Stage 2 validator."""

    dbms: str
    command: tuple[str, ...]
    coverage_command: tuple[str, ...] | None = None
    timeout_seconds: float = 30.0
    coverage_timeout_seconds: float = 10.0
    env: dict[str, str] = field(default_factory=dict)


def load_executor_config(dbms: str) -> ExecutorConfig:
    """Load one DBMS executor without embedding an experiment-machine path.

    The optional configuration is either a JSON file named by
    ``SQLEEK_STAGE2_EXECUTOR_CONFIG`` or a JSON object in
    ``SQLEEK_STAGE2_EXECUTORS``.  Its top-level keys are DBMS names, for
    example::

        {"postgresql": {"command": ["psql", "--dbname", "sqleek"]}}

    ``coverage_command`` is optional.  Both commands receive the candidate SQL
    on stdin; ``SQLEEK_STAGE2_DBMS`` is exported for coverage helpers.
    """

    dbms_key = str(dbms).strip().lower()
    defaults = dict(DEFAULT_EXECUTORS.get(dbms_key, {"command": [dbms_key]}))
    overrides = _load_executor_overrides().get(dbms_key, {})
    if not isinstance(overrides, Mapping):
        raise ValueError(f"executor configuration for {dbms_key!r} must be an object")
    merged = {**defaults, **dict(overrides)}

    command = _normalise_command(merged.get("command"))
    if not command:
        raise ValueError(f"no executor command configured for DBMS {dbms_key!r}")
    coverage_command = _normalise_command(merged.get("coverage_command"))
    env_value = merged.get("env") or {}
    if not isinstance(env_value, Mapping):
        raise ValueError(f"executor env for {dbms_key!r} must be an object")

    return ExecutorConfig(
        dbms=dbms_key,
        command=command,
        coverage_command=coverage_command or None,
        timeout_seconds=_positive_float(merged.get("timeout_seconds", 30.0), 30.0),
        coverage_timeout_seconds=_positive_float(merged.get("coverage_timeout_seconds", 10.0), 10.0),
        env={str(k): str(v) for k, v in env_value.items()},
    )


def validate_and_repair_seed(
    sql: str,
    *,
    dbms: str,
    client: Any,
    repair_fn: Callable[..., Mapping[str, Any]],
    context: Mapping[str, Any] | None = None,
    executor: ExecutorConfig | None = None,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    candidate_id: str = "",
) -> dict[str, Any]:
    """Execute a concrete SQL candidate and repair it at most three times."""

    config = executor or load_executor_config(dbms)
    rounds = max(0, min(int(max_repair_rounds), MAX_REPAIR_ROUNDS))
    current_sql = str(sql or "").strip() or "SELECT 1;"
    attempts: list[dict[str, Any]] = []

    for repair_round in range(rounds + 1):
        result = _execute_candidate(current_sql, config, candidate_id=candidate_id)
        execution = result["execution"]
        coverage = result["coverage"]
        attempt: dict[str, Any] = {
            "round": repair_round,
            "sql": current_sql,
            "validated": bool(result["validated"]),
            "diagnostics": execution,
            "coverage": coverage,
        }
        attempts.append(attempt)

        if result["validated"]:
            return {
                "validated": True,
                "sql": current_sql,
                "repair_rounds": repair_round,
                "attempts": attempts,
                "diagnostics": execution,
                "coverage": coverage,
            }

        if repair_round >= rounds:
            break

        try:
            repaired = repair_fn(
                client=client,
                dbms=config.dbms,
                sql=current_sql,
                diagnostics=execution,
                coverage=coverage,
                context=dict(context or {}),
                round_index=repair_round + 1,
            )
        except Exception as exc:  # Keep this candidate rejected, never write it.
            attempt["repair_error"] = str(exc)
            break

        repaired_sql = ""
        if isinstance(repaired, Mapping):
            repaired_sql = str(repaired.get("sql") or repaired.get("template") or "").strip()
            if repaired.get("reasoning"):
                attempt["repair_reasoning"] = str(repaired["reasoning"])
        if not repaired_sql:
            attempt["repair_error"] = "repair response did not contain non-empty sql"
            break
        current_sql = repaired_sql

    return {
        "validated": False,
        "sql": current_sql,
        "repair_rounds": len(attempts) - 1,
        "attempts": attempts,
        "diagnostics": attempts[-1].get("diagnostics", {}) if attempts else {},
        "coverage": attempts[-1].get("coverage", {}) if attempts else {},
    }


def _execute_candidate(sql: str, config: ExecutorConfig, *, candidate_id: str = "") -> dict[str, Any]:
    execution = _run_command(
        config.command,
        sql,
        timeout_seconds=config.timeout_seconds,
        env={
            **config.env,
            "SQLEEK_STAGE2_DBMS": config.dbms,
            "SQLEEK_STAGE2_CANDIDATE_ID": candidate_id,
        },
    )
    execution["error_kind"] = _classify_error(execution)

    if config.coverage_command:
        coverage_run = _run_command(
            config.coverage_command,
            sql,
            timeout_seconds=config.coverage_timeout_seconds,
            env={
                **config.env,
                "SQLEEK_STAGE2_DBMS": config.dbms,
                "SQLEEK_STAGE2_CANDIDATE_ID": candidate_id,
            },
        )
        coverage = {
            "available": True,
            "ok": bool(coverage_run["ok"]),
            **coverage_run,
        }
    else:
        coverage = {
            "available": False,
            "ok": False,
            "reason": "not_configured",
        }

    return {
        "validated": bool(execution["ok"]),
        "execution": execution,
        "coverage": coverage,
    }


def _run_command(
    command: tuple[str, ...],
    sql: str,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> dict[str, Any]:
    started = time.monotonic()
    merged_env = os.environ.copy()
    merged_env.update({str(k): str(v) for k, v in env.items()})
    try:
        completed = subprocess.run(
            list(command),
            input=sql,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=merged_env,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout_tail": (completed.stdout or "")[-_TAIL_LIMIT:],
            "stderr_tail": (completed.stderr or "")[-_TAIL_LIMIT:],
            "timed_out": False,
            "command": list(command),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout_tail": _as_text(exc.stdout)[-_TAIL_LIMIT:],
            "stderr_tail": _as_text(exc.stderr)[-_TAIL_LIMIT:],
            "timed_out": True,
            "command": list(command),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "timed_out": False,
            "command": list(command),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "timed_out": False,
            "command": list(command),
            "duration_seconds": round(time.monotonic() - started, 3),
        }


def _classify_error(result: Mapping[str, Any]) -> str | None:
    if result.get("ok"):
        return None
    if result.get("timed_out"):
        return "timeout"
    text = f"{result.get('stdout_tail', '')}\n{result.get('stderr_tail', '')}".lower()
    if any(re.search(pattern, text) for pattern in (r"syntax", r"parse error", r"near .* syntax", r"invalid .* clause", r"unsupported .* clause")):
        return "clause"
    if any(
        re.search(pattern, text)
        for pattern in (
            r"unknown table",
            r"unknown column",
            r"does not exist",
            r"no such table",
            r"no such column",
            r"unknown relation",
            r"object .* not found",
        )
    ):
        return "object_binding"
    if any(
        re.search(pattern, text)
        for pattern in (
            r"must be .* before",
            r"prepared statement",
            r"cursor .* invalid",
            r"transaction .* (?:order|sequence)",
            r"dependency .* order",
            r"statement .* order",
        )
    ):
        return "statement_ordering"
    if "no such file or directory" in text or "not found" in text:
        return "executor"
    return "execution"


def _load_executor_overrides() -> dict[str, Any]:
    raw_path = os.environ.get("SQLEEK_STAGE2_EXECUTOR_CONFIG", "").strip()
    raw_json = os.environ.get("SQLEEK_STAGE2_EXECUTORS", "").strip()
    if raw_path:
        data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    elif raw_json:
        data = json.loads(raw_json)
    else:
        return {}
    if isinstance(data, Mapping) and isinstance(data.get("executors"), Mapping):
        data = data["executors"]
    if not isinstance(data, Mapping):
        raise ValueError("Stage 2 executor configuration must be a JSON object")
    return {str(k).lower(): v for k, v in data.items()}


def _normalise_command(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(shlex.split(value))
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
