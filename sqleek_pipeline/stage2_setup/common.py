"""Shared paths and logging for Stage 2 seed generation."""
from __future__ import annotations

import os
from pathlib import Path

from config import TARGETS_DIR as CONFIG_TARGETS_DIR

ROOT = Path(os.environ.get("SQLEEK_ROOT", Path(__file__).resolve().parents[2])).expanduser().resolve()
STAGE_DIR = ROOT / "sqleek_pipeline/stage2_setup"


def _rooted_override(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    candidate = Path(value).expanduser().resolve()
    if os.environ.get("SQLEEK_ALLOW_EXTERNAL_OUTPUT", "0").lower() in {"1", "true", "yes"}:
        return candidate
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{name} must stay under {ROOT}: {candidate}") from exc
    return candidate


# RQ4 w/o-M1 can provide an alternate, isolated Stage-1 target artifact.
TARGETS_DIR = _rooted_override("SQLEEK_STAGE1_TARGET_DIR", CONFIG_TARGETS_DIR)
OUTPUT_DIR = _rooted_override("SQLEEK_STAGE2_OUTPUT_DIR", STAGE_DIR / "output")
SEEDS_DIR = OUTPUT_DIR / "seeds"
CHAINS_JSON = TARGETS_DIR / "callchains.json"
PHI_JSON = TARGETS_DIR / "phi_mapping.json"

ROOT_BUILD_LOG = ROOT / "build.log"


def log(msg: str) -> None:
    ROOT_BUILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ROOT_BUILD_LOG.open("a", encoding="utf-8") as fp:
        fp.write(f"[stage2/gen_seeds] {msg}\n")
    print(f"[stage2/gen_seeds] {msg}")


def default_source_root(dbms: str) -> str:
    return str(ROOT / "sources" / dbms / "src")
