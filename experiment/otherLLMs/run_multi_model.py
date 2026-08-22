#!/usr/bin/env python3
"""Run the same SQLeek campaign for several OpenAI-compatible LLMs.

Each model/DBMS/repeat receives an isolated output directory.  The default
command runs the existing SQLeek entrypoint; ``--command`` can replace it
with a cluster-specific launcher.  The command template can use:

    {repo_root} {run_dir} {stage2_dir} {fuzz_dir} {seed_dir}
    {dbms} {run_id} {duration} {model_key} {model} {repeat}

Model credentials are read only from environment variables named in
``models.json``.  They are never written to metadata or result files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from result_io import (
    append_jsonl,
    count_seed_files,
    discover_bug_count,
    read_usage_log,
    utc_now,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG = SCRIPT_DIR / "models.json"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    provider: str
    model_default: str
    model_env: str
    base_url_default: str
    base_url_env: str
    api_key_env: str
    input_price_env: str = ""
    output_price_env: str = ""
    enabled: bool = True

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ModelSpec":
        required = (
            "key",
            "label",
            "model_default",
            "model_env",
            "base_url_default",
            "base_url_env",
            "api_key_env",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"model entry is missing keys: {', '.join(missing)}")
        return cls(
            key=str(payload["key"]),
            label=str(payload["label"]),
            provider=str(payload.get("provider", "openai-compatible")),
            model_default=str(payload["model_default"]),
            model_env=str(payload["model_env"]),
            base_url_default=str(payload["base_url_default"]),
            base_url_env=str(payload["base_url_env"]),
            api_key_env=str(payload["api_key_env"]),
            input_price_env=str(payload.get("input_price_env", "")),
            output_price_env=str(payload.get("output_price_env", "")),
            enabled=bool(payload.get("enabled", True)),
        )

    def resolve(self, inherited_env: dict[str, str]) -> dict[str, Any]:
        model = inherited_env.get(self.model_env, "").strip() or self.model_default.strip()
        base_url = inherited_env.get(self.base_url_env, "").strip()
        if not base_url:
            base_url = inherited_env.get("OPENAI_BASE_URL", "").strip() or self.base_url_default.strip()
        api_key = inherited_env.get(self.api_key_env, "").strip()
        if not api_key:
            api_key = inherited_env.get("OPENAI_API_KEY", "").strip()
        return {
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "input_price_usd_per_million": _nonnegative_float(inherited_env.get(self.input_price_env, "")),
            "output_price_usd_per_million": _nonnegative_float(inherited_env.get(self.output_price_env, "")),
        }


def _nonnegative_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def load_models(path: Path) -> list[ModelSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_models = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        raise ValueError(f"expected a 'models' list in {path}")
    models = [ModelSpec.from_json(item) for item in raw_models if isinstance(item, dict)]
    return [model for model in models if model.enabled]


def parse_dbms(raw_values: list[str]) -> list[str]:
    values: list[str] = []
    for raw in raw_values:
        for item in raw.split(","):
            item = item.strip().lower()
            if item == "postgresql":
                item = "postgres"
            if item and item not in values:
                values.append(item)
    if not values:
        raise ValueError("at least one DBMS is required")
    return values


def format_command(template: str, context: dict[str, Any]) -> str:
    # Quote values before interpolation so paths with spaces remain safe while
    # still allowing the command itself to contain shell operators.
    quoted = {key: shlex.quote(str(value)) for key, value in context.items()}
    rendered = template
    for key, value in quoted.items():
        rendered = rendered.replace("{" + key + "}", value)
    unknown = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", rendered)))
    if unknown:
        raise ValueError(f"unknown command placeholder: {unknown[0]}")
    return rendered


def format_path(template: str, context: dict[str, Any]) -> str:
    """Interpolate a filesystem template without shell quoting."""
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    unknown = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", rendered)))
    if unknown:
        raise ValueError(f"unknown path placeholder: {unknown[0]}")
    return rendered


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--models", default="", help="comma-separated model keys; default: all configured models")
    parser.add_argument("--dbms", action="append", default=[], help="DBMS or comma-separated DBMS list")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--duration", default="24h")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--command",
        default=None,
        help="shell command template; default: bash {repo_root}/run.sh {dbms} {run_id} {duration}",
    )
    parser.add_argument(
        "--post-command",
        default=None,
        help="optional triage command template run after the campaign; same placeholders as --command",
    )
    parser.add_argument("--stage2-only", action="store_true", help="generate model-specific seeds without starting the fuzzer")
    parser.add_argument("--bug-report", default="", help="optional report path/template; auto-discovery is used otherwise")
    parser.add_argument("--resume", action="store_true", help="skip run directories that already contain metadata.json")
    parser.add_argument("--dry-run", action="store_true", help="write no runs; print the commands and credential status")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--max-entries", type=int, default=10)
    parser.add_argument("--top-templates", type=int, default=5)
    parser.add_argument("--variants-per-template", type=int, default=10)
    return parser


def validate_model_credentials(spec: ModelSpec, resolved: dict[str, str], *, dry_run: bool) -> None:
    missing = []
    if not resolved["model"]:
        missing.append(f"{spec.model_env} (model id)")
    if not resolved["base_url"]:
        missing.append(f"{spec.base_url_env} or OPENAI_BASE_URL")
    if not resolved["api_key"]:
        missing.append(f"{spec.api_key_env} or OPENAI_API_KEY")
    if missing and not dry_run:
        raise ValueError(f"{spec.label}: missing configuration: {', '.join(missing)}")


def run_one(
    *,
    spec: ModelSpec,
    resolved: dict[str, str],
    dbms: str,
    repeat: int,
    args: argparse.Namespace,
    repo_root: Path,
    output_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    run_id = f"otherllm_{spec.key}_{dbms}_r{repeat:02d}"
    run_dir = output_root / spec.key / dbms / f"r{repeat:02d}"
    stage2_dir = run_dir / "stage2"
    fuzz_dir = run_dir / "fuzz"
    seed_dir = stage2_dir / "seeds" / dbms / "memory"
    usage_log = run_dir / "llm_usage.jsonl"
    runner_log = run_dir / "runner.log"
    post_log = run_dir / "post_command.log"
    metadata_path = run_dir / "metadata.json"
    result_path = run_dir / "result.json"

    command_template = args.command
    if args.stage2_only:
        command_template = "python3 {repo_root}/sqleek_pipeline/stage2_setup/gen_seeds.py"
    if command_template is None:
        command_template = "bash {repo_root}/run.sh {dbms} {run_id} {duration}"

    context = {
        "repo_root": repo_root,
        "run_dir": run_dir,
        "stage2_dir": stage2_dir,
        "fuzz_dir": fuzz_dir,
        "seed_dir": seed_dir,
        "dbms": dbms,
        "run_id": run_id,
        "duration": args.duration,
        "model_key": spec.key,
        "model": resolved["model"],
        "repeat": repeat,
    }
    command = format_command(command_template, context)
    post_command = format_command(args.post_command, context) if args.post_command else ""

    if metadata_path.exists() and args.resume:
        print(f"[skip] {run_dir} (metadata.json exists)")
        return {"run_dir": str(run_dir), "status": "skipped_existing", "model_key": spec.key, "dbms": dbms}
    if run_dir.exists() and not args.dry_run:
        raise FileExistsError(f"refusing to overwrite existing run directory: {run_dir}; use --resume to skip it")

    credential_status = {
        "model_configured": bool(resolved["model"]),
        "base_url_configured": bool(resolved["base_url"]),
        "api_key_configured": bool(resolved["api_key"]),
    }
    metadata = {
        "schema_version": 1,
        "experiment": "sqleek_multi_model",
        "model_key": spec.key,
        "model_label": spec.label,
        "model": resolved["model"],
        "provider": spec.provider,
        "base_url": resolved["base_url"],
        "api_key_configured": credential_status["api_key_configured"],
        "input_price_usd_per_million": resolved.get("input_price_usd_per_million"),
        "output_price_usd_per_million": resolved.get("output_price_usd_per_million"),
        "dbms": dbms,
        "repeat": repeat,
        "run_id": run_id,
        "duration": args.duration,
        "command": command,
        "post_command": post_command,
        "stage2_dir": str(stage2_dir),
        "fuzz_dir": str(fuzz_dir),
        "seed_dir": str(seed_dir),
        "usage_log": str(usage_log),
        "credential_status": credential_status,
        "created_at": utc_now(),
    }

    if args.dry_run:
        print(json.dumps({"run_dir": str(run_dir), **metadata}, ensure_ascii=False, indent=2))
        return {**metadata, "run_dir": str(run_dir), "status": "dry_run"}

    validate_model_credentials(spec, resolved, dry_run=False)
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "command.sh").write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{command}\n", encoding="utf-8")
    (run_dir / "command.sh").chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "SQLEEK_ROOT": str(repo_root),
            "SQLEEK_ALLOW_EXTERNAL_OUTPUT": "1",
            "SQLEEK_LLM_ENABLED": "1",
            "SQLEEK_LLM_PROVIDER": "openai",
            "OPENAI_MODEL": resolved["model"],
            "OPENAI_BASE_URL": resolved["base_url"],
            "OPENAI_API_KEY": resolved["api_key"],
            "SQLEEK_LLM_USAGE_LOG": str(usage_log),
            "SQLEEK_LLM_DBMS": dbms,
            "SQLEEK_STAGE2_OUTPUT_DIR": str(stage2_dir),
            "SQLEEK_SEED_DIR": str(seed_dir),
            "SQLEEK_RUN_OUTPUT_ROOT": str(fuzz_dir),
            "SQLEEK_EXPERIMENT_MODEL_KEY": spec.key,
            "SQLEEK_EXPERIMENT_RUN_DIR": str(run_dir),
            "STAGE2_MAX_ENTRIES": str(args.max_entries),
            "STAGE2_TOP_TEMPLATES": str(args.top_templates),
            "STAGE2_VARIANTS_PER_TEMPLATE": str(args.variants_per_template),
            "SQLEEK_FUZZ_OUTPUT_DIR": str(fuzz_dir),
            "SQLEEK_TRIAGE_OUTPUT_DIR": str(run_dir / "triage"),
            "SQLEEK_TRIAGE_RUNTIME_CONFIG": str(run_dir / "triage_runtime.json"),
        }
    )

    started_at = utc_now()
    print(f"[run] {spec.label} dbms={dbms} repeat={repeat}: {command}")
    with runner_log.open("w", encoding="utf-8") as log_fp:
        process = subprocess.run(
            command,
            cwd=repo_root,
            env=env,
            shell=True,
            executable="/bin/bash",
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            check=False,
        )
    post_process = None
    if post_command:
        print(f"[post] {spec.label} dbms={dbms} repeat={repeat}: {post_command}")
        with post_log.open("w", encoding="utf-8") as log_fp:
            post_process = subprocess.run(
                post_command,
                cwd=repo_root,
                env=env,
                shell=True,
                executable="/bin/bash",
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                check=False,
            )
    ended_at = utc_now()

    usage = read_usage_log(usage_log)
    cost_usd = _estimate_cost(usage, resolved)
    report_hint = ""
    if args.bug_report:
        report_hint = format_path(args.bug_report, context).strip()
        if not Path(report_hint).is_absolute():
            report_hint = str((repo_root / report_hint).resolve())
    bug = discover_bug_count(run_dir, report_hint or None)
    overall_status = "completed" if process.returncode == 0 and (post_process is None or post_process.returncode == 0) else "failed"

    result: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "sqleek_multi_model",
        "model_key": spec.key,
        "model_label": spec.label,
        "model": resolved["model"],
        "dbms": dbms,
        "repeat": repeat,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": overall_status,
        "exit_code": process.returncode,
        "post_exit_code": post_process.returncode if post_process is not None else None,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": _elapsed_seconds(started_at, ended_at),
        "request_count": usage.request_count,
        "usage_record_count": usage.usage_record_count,
        "missing_usage_count": usage.missing_usage_count,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": cost_usd,
        "cost_note": (
            "estimated from provider-reported prompt/completion tokens and configured input/output rates"
            if cost_usd is not None
            else "unavailable: configure input/output USD-per-million rates and retain prompt/completion usage"
        ),
        "seed_count": count_seed_files(run_dir),
        "unique_bug_count": bug.count,
        "bug_count_source": bug.source,
        "bug_report_path": bug.path,
        "bug_count_note": bug.note,
        "usage_log": str(usage_log),
        "post_log": str(post_log) if post_command else None,
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_jsonl(manifest_path, result)
    print(
        f"[done] {spec.label} dbms={dbms} repeat={repeat} "
        f"status={result['status']} tokens={result['total_tokens']} bugs={result['unique_bug_count']}"
    )
    return result


def _elapsed_seconds(started_at: str, ended_at: str) -> float | None:
    from datetime import datetime

    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        return round((end - start).total_seconds(), 3)
    except ValueError:
        return None


def _estimate_cost(usage: Any, resolved: dict[str, Any]) -> float | None:
    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    input_rate = resolved.get("input_price_usd_per_million")
    output_rate = resolved.get("output_price_usd_per_million")
    if prompt_tokens is None or completion_tokens is None or input_rate is None or output_rate is None:
        return None
    return round(
        (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000,
        8,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = (args.repo_root or Path(os.environ.get("SQLEEK_ROOT", DEFAULT_REPO_ROOT))).expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    output_root = (args.output_root or repo_root / "experiment/otherLLMs/results").expanduser().resolve()

    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")
    if args.max_entries == 0 or args.top_templates == 0 or args.variants_per_template == 0:
        raise SystemExit("Stage 2 limits must be non-zero")

    models = load_models(config_path)
    requested_models = {item.strip() for item in args.models.split(",") if item.strip()}
    if requested_models:
        models = [model for model in models if model.key in requested_models]
    if not models:
        raise SystemExit("no configured models selected")
    dbms_values = parse_dbms(args.dbms or ["postgres"])

    inherited_env = dict(os.environ)
    manifest_path = output_root / "results.jsonl"
    all_results: list[dict[str, Any]] = []
    for spec in models:
        resolved = spec.resolve(inherited_env)
        validate_model_credentials(spec, resolved, dry_run=args.dry_run)
        for dbms in dbms_values:
            for repeat in range(1, args.repeats + 1):
                try:
                    result = run_one(
                        spec=spec,
                        resolved=resolved,
                        dbms=dbms,
                        repeat=repeat,
                        args=args,
                        repo_root=repo_root,
                        output_root=output_root,
                        manifest_path=manifest_path,
                    )
                    all_results.append(result)
                except Exception as exc:
                    print(f"[error] {spec.key} dbms={dbms} repeat={repeat}: {exc}", file=sys.stderr)
                    if not args.continue_on_error:
                        return 1

    print(f"Prepared/executed {len(all_results)} run(s); manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
