"""Shared I/O and accounting helpers for the multi-model experiment.

The experiment deliberately keeps model-request accounting separate from the
fuzzer output.  A run is considered comparable only when both its reported
LLM token usage and its deduplicated bug count can be recovered.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TOKEN_FIELDS = {
    "prompt_tokens": ("prompt_tokens", "input_tokens"),
    "completion_tokens": ("completion_tokens", "output_tokens"),
    "total_tokens": ("total_tokens", "tokens"),
}

BUG_COUNT_KEYS = (
    "unique_bug_count",
    "unique_bugs",
    "unique_crashes",
    "bug_count",
    "bugs_found",
    "discovered_bugs",
)


@dataclass(frozen=True)
class UsageSummary:
    request_count: int
    usage_record_count: int
    missing_usage_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class BugCount:
    count: int | None
    source: str
    path: str | None
    note: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _token_value(payload: dict[str, Any], field: str) -> int | None:
    for name in TOKEN_FIELDS[field]:
        value = _as_nonnegative_int(payload.get(name))
        if value is not None:
            return value
    return None


def _usage_payload(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    nested = record.get("usage")
    if isinstance(nested, dict):
        merged = dict(record)
        merged.update(nested)
        return merged
    return record


def read_usage_log(path: Path) -> UsageSummary:
    """Read the JSONL emitted by ``sqleek_pipeline.llm.client``.

    Providers occasionally omit usage for an error or a streaming response;
    those requests remain visible in ``missing_usage_count`` instead of being
    silently treated as zero tokens.
    """
    if not path.exists():
        return UsageSummary(0, 0, 0, None, None, None)

    request_count = 0
    usage_record_count = 0
    missing_usage_count = 0
    prompt_total = 0
    completion_total = 0
    total_total = 0
    prompt_seen = False
    completion_seen = False
    total_seen = False

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            continue
        request_count += 1
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            missing_usage_count += 1
            continue

        payload = _usage_payload(record)
        prompt = _token_value(payload, "prompt_tokens")
        completion = _token_value(payload, "completion_tokens")
        total = _token_value(payload, "total_tokens")
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion

        if prompt is not None:
            prompt_seen = True
            prompt_total += prompt
        if completion is not None:
            completion_seen = True
            completion_total += completion
        if total is not None:
            total_seen = True
            total_total += total

        if prompt is None and completion is None and total is None:
            missing_usage_count += 1
        else:
            usage_record_count += 1

    return UsageSummary(
        request_count=request_count,
        usage_record_count=usage_record_count,
        missing_usage_count=missing_usage_count,
        prompt_tokens=prompt_total if prompt_seen else None,
        completion_tokens=completion_total if completion_seen else None,
        total_tokens=total_total if total_seen else None,
    )


def _number_from_mapping(payload: dict[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        value = _as_nonnegative_int(payload.get(key))
        if value is not None:
            return value
    return None


def _bug_count_from_json(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None

    direct = _number_from_mapping(payload, BUG_COUNT_KEYS)
    if direct is not None:
        return direct

    for key in ("bugs", "unique_bug_ids", "logic_bugs", "findings"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            return len(value)

    summary = payload.get("summary")
    if isinstance(summary, dict):
        direct = _number_from_mapping(summary, BUG_COUNT_KEYS)
        if direct is not None:
            return direct

    return None


def _bug_count_from_csv(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fp:
            sample = fp.read(4096)
            fp.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
            rows = list(csv.DictReader(fp, dialect=dialect))
    except (OSError, csv.Error):
        return None

    if not rows:
        return 0
    normalized = {str(key).strip().lower(): key for key in rows[0] if key is not None}
    for candidate in (
        "unique_bug_count",
        "unique_bugs",
        "bug_count",
        "bugs_found",
    ):
        key = normalized.get(candidate)
        if key is not None:
            values = [_as_nonnegative_int(row.get(key)) for row in rows]
            values = [value for value in values if value is not None]
            if values:
                return values[-1]

    for candidate in (
        "bug_id",
        "bug",
        "id",
        "signature",
        "stack_hash",
        "crash_id",
    ):
        key = normalized.get(candidate)
        if key is not None:
            values = {str(row.get(key, "")).strip() for row in rows}
            values.discard("")
            return len(values)
    return len(rows)


def extract_bug_count(path: Path) -> BugCount:
    """Extract a deduplicated bug count from a JSON/CSV/TSV/text report."""
    try:
        suffix = path.suffix.lower()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return BugCount(None, "unreadable", str(path), str(exc))

    if suffix == ".json":
        try:
            count = _bug_count_from_json(json.loads(text))
        except json.JSONDecodeError:
            count = None
        if count is not None:
            return BugCount(count, "json", str(path))
    elif suffix in {".csv", ".tsv"}:
        count = _bug_count_from_csv(path)
        if count is not None:
            return BugCount(count, suffix[1:], str(path))

    patterns = (
        r"unique\s+(?:bugs?|crashes?)\s*[:=]\s*(\d+)",
        r"(?:deduplicated|discovered|found)\s+bugs?\s*[:=]\s*(\d+)",
        r"bug_count\s*[:=]\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return BugCount(int(match.group(1)), "text", str(path))

    return BugCount(None, "unrecognized", str(path))


def discover_bug_count(run_dir: Path, report_hint: str | None = None) -> BugCount:
    """Find the most authoritative bug report under one isolated run dir."""
    candidates: list[Path] = []
    if report_hint:
        hinted = Path(report_hint).expanduser()
        if hinted.exists():
            candidates.append(hinted)

    preferred_names = (
        "crash_report.json",
        "bug_report.json",
        "unique_bugs.json",
        "bugs.csv",
        "bug_report.csv",
        "crash_report.csv",
    )
    for name in preferred_names:
        candidates.extend(sorted(run_dir.rglob(name)))

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        result = extract_bug_count(candidate)
        if result.count is not None:
            return result

    return BugCount(None, "missing", None, "No deduplicated bug report was found under the run directory")


def count_seed_files(run_dir: Path) -> int | None:
    """Count generated SQL seeds when the isolated Stage 2 output is present."""
    seed_root = run_dir / "stage2" / "seeds"
    if not seed_root.exists():
        return None
    return sum(1 for path in seed_root.rglob("*.sql") if path.is_file() and path.stat().st_size > 0)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def write_csv(path: Path, records: list[dict[str, Any]], fieldnames: Iterable[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames or [])
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    if not fields:
        path.write_text("\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None
