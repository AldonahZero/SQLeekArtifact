#!/usr/bin/env python3
"""Stage 2 entrypoint: LLM-inferred SQL seed generation."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqleek_pipeline.stage2_setup.common import log
from sqleek_pipeline.stage2_setup.pipeline import main


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        log(traceback.format_exc())
        raise
