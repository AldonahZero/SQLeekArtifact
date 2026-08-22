"""Shared output paths for RQ2 figure generation scripts."""
from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = BASE_DIR / "result"
FIG_BASE = RESULT_DIR / "figures"
HEATMAP_DIR = FIG_BASE / "heatmaps"
HOUR24_DIR = FIG_BASE / "24h"

LEGACY_SUBDIRS = ("热力图", "vn图", "24h图")


def dbms_slug(dbms: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", dbms.lower()).strip("_")


def dbms_heatmap_dir(dbms: str) -> Path:
    return HEATMAP_DIR / dbms_slug(dbms)


def cleanup_legacy_flat_figures() -> None:
    """Remove rq2_* figures left in figures/ before subfolder layout."""
    if not FIG_BASE.exists():
        return
    for path in FIG_BASE.glob("rq2_*"):
        if path.is_file():
            path.unlink()


def cleanup_legacy_subdirs() -> None:
    """Remove old Chinese-named figure subdirectories after layout rename."""
    import shutil

    if not FIG_BASE.exists():
        return
    for name in LEGACY_SUBDIRS:
        path = FIG_BASE / name
        if path.exists():
            shutil.rmtree(path)


def ensure_fig_dirs(*, dbms_names: list[str] | None = None) -> None:
    cleanup_legacy_flat_figures()
    cleanup_legacy_subdirs()
    HEATMAP_DIR.mkdir(parents=True, exist_ok=True)
    HOUR24_DIR.mkdir(parents=True, exist_ok=True)
    for dbms in dbms_names or []:
        dbms_heatmap_dir(dbms).mkdir(parents=True, exist_ok=True)


def iter_generated_figures() -> list[Path]:
    paths: list[Path] = []
    for root in (HEATMAP_DIR, HOUR24_DIR):
        if root.exists():
            paths.extend(sorted(root.rglob("rq2_*")))
    return paths
