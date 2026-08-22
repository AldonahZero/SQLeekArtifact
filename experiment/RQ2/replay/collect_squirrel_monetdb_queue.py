#!/usr/bin/env python3
"""Create flat, read-only hard-link snapshots of nested SQUIRREL queues."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.tsv"
    rows: list[tuple[str, Path]] = []
    for repeat in sorted(args.source.glob("r[1-5]")):
        if not repeat.is_dir():
            continue
        for src in sorted(repeat.glob("**/queue/*")):
            if src.is_file():
                rows.append((repeat.name, src))
    with manifest.open("w", encoding="utf-8") as fp:
        for index, (repeat, src) in enumerate(rows):
            digest = hashlib.sha1(str(src).encode()).hexdigest()[:12]
            dst = args.out / f"{index:08d}_{repeat}_{digest}_{src.name}"
            if not dst.exists():
                os.link(src, dst)
            fp.write(f"{repeat}\t{src}\t{dst}\n")
    print(f"files={len(rows)}")


if __name__ == "__main__":
    main()
