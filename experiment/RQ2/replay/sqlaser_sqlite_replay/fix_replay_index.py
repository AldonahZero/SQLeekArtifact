#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path


FORMAL = {
    "r1": "r1_20260711_174208",
    "r2": "r2_20260711_184300",
    "r3": "r3_20260711_184300",
    "r4": "r4_20260711_184300",
    "r5": "r5_20260711_184300",
}


def line_count(path: Path) -> int:
    with path.open() as fp:
        return sum(1 for _ in fp)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: fix_replay_index.py <replay-root>")
    root = Path(sys.argv[1])
    raw = root / "replay_index.tsv"
    out = root / "replay_index.host.tsv"
    with raw.open(newline="") as fp, out.open("w", newline="") as gp:
        reader = csv.DictReader(fp, delimiter="\t")
        writer = csv.DictWriter(gp, fieldnames=reader.fieldnames, delimiter="\t")
        writer.writeheader()
        for row in reader:
            rep = "r" + row["repeat_id"]
            for key in ("cov_json", "profdata", "report"):
                val = row[key]
                if val.startswith("/rq2_out/"):
                    row[key] = str(root / "repeats" / rep / val.removeprefix("/rq2_out/"))
            seed_corpus = row["seed_corpus"]
            if seed_corpus.startswith("/rq2_checkpoints/"):
                row["seed_corpus"] = str(
                    Path("/root/SQLeek/experiment/RQ2/sqlaser/results/sqlite354/formal_24h")
                    / FORMAL[rep]
                    / "checkpoint_replay"
                    / seed_corpus.removeprefix("/rq2_checkpoints/")
                )
            checkpoint_dir = "checkpoint_" + str(int(row["checkpoint_min"])).zfill(4) + "m"
            seed_file = root / "repeats" / rep / checkpoint_dir / "manifests" / "executed_seeds_cumulative.tsv"
            if seed_file.exists():
                row["seed_count"] = str(max(0, line_count(seed_file) - 1))
            missing = [key for key in ("cov_json", "profdata", "report") if not Path(row[key]).exists()]
            if missing:
                row["status"] = "missing"
                row["message"] = "missing:" + ",".join(missing)
            writer.writerow(row)
    print(out)


if __name__ == "__main__":
    main()
