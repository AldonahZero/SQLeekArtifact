#!/usr/bin/env python3
"""Expand final-only PG replay rows to all retained checkpoint rows."""

from __future__ import annotations

import sys
from pathlib import Path


CHECKPOINTS = (60, 180, 300, 480, 600, 720, 900, 1200, 1440)
HEADER = (
    "run_id\ttool\tdbms\trepeat_id\tcheckpoint_min\tcov_json\treport_txt\t"
    "status\tmessage\tcontainer_image\tbinary\tseed_count\tbuild_id\t"
    "container_id\tversion\tstart_time\tend_time\n"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rebuild_pg_replay_index.py REPLAY_ROOT")
    root = Path(sys.argv[1]).resolve()
    rows: list[str] = []
    for repeat in (1, 2, 4, 5):
        row_file = root / "postgres" / f"r{repeat}" / "replay_index_row.tsv"
        if not row_file.exists():
            continue
        for line in row_file.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) != 17:
                continue
            for checkpoint in CHECKPOINTS:
                current = list(fields)
                current[4] = str(checkpoint)
                current[5] = current[5].replace("_t1440.", f"_t{checkpoint}.")
                current[6] = current[6].replace("_t1440.", f"_t{checkpoint}.")
                profdata = Path(current[5].replace(".cov.json", ".profdata"))
                current[7] = "complete" if profdata.is_file() else "failed"
                rows.append("\t".join(current) + "\n")
    if not rows:
        raise SystemExit(f"no replay rows found under {root}")
    (root / "postgres" / "replay_index.tsv").write_text(
        HEADER + "".join(rows), encoding="utf-8"
    )
    print(f"wrote {len(rows)} checkpoint rows")


if __name__ == "__main__":
    main()
