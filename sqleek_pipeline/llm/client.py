"""OpenAI-backed LLM client shared by SQLeek pipeline stages."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openai import OpenAI


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / "config.env"
DEFAULT_SKILL_DIR = Path(__file__).resolve().parent / "skills"


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    skill_dir: Path
    usage_log: Path | None


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, file_values: dict[str, str], default: str = "") -> str:
    return os.getenv(name, file_values.get(name, default))


def _first_int(payload: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = payload.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def load_llm_config(env_path: Path | str = DEFAULT_ENV_PATH) -> LLMConfig:
    file_values = _load_env_file(Path(env_path))
    skill_dir = Path(_env("SQLEEK_LLM_SKILL_DIR", file_values, str(DEFAULT_SKILL_DIR)))
    usage_log_value = _env("SQLEEK_LLM_USAGE_LOG", file_values)
    return LLMConfig(
        enabled=_env("SQLEEK_LLM_ENABLED", file_values, "0").lower() in {"1", "true", "yes"},
        provider=_env("SQLEEK_LLM_PROVIDER", file_values, "openai"),
        api_key=_env("OPENAI_API_KEY", file_values),
        base_url=_env("OPENAI_BASE_URL", file_values, "https://api.openai.com/v1"),
        model=_env("OPENAI_MODEL", file_values, "gpt-4.1"),
        timeout_seconds=float(_env("OPENAI_TIMEOUT_SECONDS", file_values, "60")),
        max_retries=int(_env("OPENAI_MAX_RETRIES", file_values, "2")),
        skill_dir=skill_dir,
        usage_log=Path(usage_log_value).expanduser() if usage_log_value else None,
    )


class OpenAILLMClient:
    """Thin wrapper so every stage uses the same provider config and skills."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_llm_config()
        if self.config.provider != "openai":
            raise ValueError(f"unsupported LLM provider: {self.config.provider}")
        if self.config.enabled and not self.config.api_key:
            raise ValueError("SQLEEK_LLM_ENABLED=1 requires OPENAI_API_KEY")
        self._client = OpenAI(
            api_key=self.config.api_key or "not-set",
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=self.config.max_retries,
        )

    def load_skill(self, skill_name: str) -> str:
        skill_path = self.config.skill_dir / f"{skill_name}.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"LLM skill not found: {skill_path}")
        return skill_path.read_text(encoding="utf-8")

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0) -> str:
        if not self.config.enabled:
            raise RuntimeError("LLM calls are disabled; set SQLEEK_LLM_ENABLED=1")

        # Ensure we never hang indefinitely even if an OpenAI-compatible endpoint is unreachable.
        # The SDK uses its own internal retry logic; we keep a hard upper bound per request here.
        try:
            response = self._client.chat.completions.create(
                model=self.config.model,
                temperature=temperature,
                timeout=self.config.timeout_seconds,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            self._record_usage(None, temperature=temperature, error=repr(exc))
            raise
        self._record_usage(response, temperature=temperature)
        return response.choices[0].message.content or ""

    def _record_usage(self, response: Any, *, temperature: float, error: str | None = None) -> None:
        """Append provider-reported token usage without affecting inference."""
        path = self.config.usage_log
        if path is None:
            return

        usage = getattr(response, "usage", None)
        if usage is None:
            usage_payload: dict[str, Any] = {}
        elif hasattr(usage, "model_dump"):
            usage_payload = usage.model_dump()
        elif hasattr(usage, "dict"):
            usage_payload = usage.dict()
        elif isinstance(usage, dict):
            usage_payload = dict(usage)
        else:
            usage_payload = {
                key: getattr(usage, key)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if getattr(usage, key, None) is not None
            }

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": getattr(response, "id", None),
            "model": getattr(response, "model", None) or self.config.model,
            "provider": self.config.provider,
            "temperature": temperature,
            "prompt_tokens": _first_int(usage_payload, "prompt_tokens", "input_tokens"),
            "completion_tokens": _first_int(usage_payload, "completion_tokens", "output_tokens"),
            "total_tokens": _first_int(usage_payload, "total_tokens"),
            "usage": usage_payload,
        }
        if error is not None:
            record["error"] = error
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Usage accounting must never turn a successful model request into
            # a failed seed-generation request.
            return

    def complete_with_skill(
        self,
        skill_name: str,
        user_prompt_parts: Iterable[str],
        *,
        temperature: float = 0.0,
    ) -> str:
        return self.complete(
            self.load_skill(skill_name),
            "\n\n".join(part for part in user_prompt_parts if part),
            temperature=temperature,
        )
