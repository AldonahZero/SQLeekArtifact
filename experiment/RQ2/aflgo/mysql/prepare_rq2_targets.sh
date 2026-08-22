#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MYSQL_SRC=${MYSQL_SRC:-/root/SQLeek/sources/mysql}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mysql}
STAGE1_TARGET_DIR=${STAGE1_TARGET_DIR:-/root/SQLeek/sqleek_pipeline/stage1_static/output/targets}
OUT_DIR=$WORK_DIR/targets
OUT=$OUT_DIR/mysql_rq2_targets.txt
RESOLVED=$OUT_DIR/mysql_rq2_targets_resolved.tsv
MANIFEST=$OUT_DIR/mysql_rq2_targets_manifest.txt

mkdir -p "$OUT_DIR"

SOURCES=(
  "$STAGE1_TARGET_DIR/mysql_memory.txt"
  "$STAGE1_TARGET_DIR/mysql_stale.txt"
  "$STAGE1_TARGET_DIR/mysql_logic.txt"
)

for src in "${SOURCES[@]}"; do
  if [ ! -f "$src" ]; then
    echo "missing Stage 1 target file: $src" >&2
    exit 1
  fi
done

python3 - "$MYSQL_SRC" "$OUT" "$RESOLVED" "${SOURCES[@]}" <<'PY'
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

mysql_src = Path(sys.argv[1])
out = Path(sys.argv[2])
resolved = Path(sys.argv[3])
sources = [Path(x) for x in sys.argv[4:]]
valid_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}

by_name: dict[str, list[Path]] = defaultdict(list)
for path in mysql_src.rglob("*"):
    if path.is_file() and path.suffix in valid_suffixes:
        by_name[path.name].append(path)

targets: set[str] = set()
rows: list[tuple[str, str, str, str]] = []
for source in sources:
    for raw in source.read_text(errors="replace").splitlines():
        text = raw.strip()
        if not text or ":" not in text:
            continue
        file_part, line_part = text.rsplit(":", 1)
        file_part = file_part.strip()
        line_part = line_part.strip()
        if not line_part.isdigit():
            continue
        suffix = Path(file_part).suffix
        if suffix not in valid_suffixes:
            continue
        target = f"{Path(file_part).name}:{int(line_part)}"
        targets.add(target)
        matches = by_name.get(Path(file_part).name, [])
        if len(matches) == 1:
            status = "unique"
            match = str(matches[0].relative_to(mysql_src))
        elif len(matches) > 1:
            status = "ambiguous"
            match = ";".join(str(p.relative_to(mysql_src)) for p in matches[:8])
        else:
            status = "missing"
            match = ""
        rows.append((source.name, text, status, match))

out.write_text("\n".join(sorted(targets)) + ("\n" if targets else ""))
with resolved.open("w") as fp:
    fp.write("stage1_file\traw_target\tstatus\tmatches\n")
    for row in rows:
        fp.write("\t".join(row) + "\n")
PY

if [ ! -s "$OUT" ]; then
  echo "no valid MySQL AFLGo targets generated" >&2
  exit 1
fi

{
  echo "target_list=$OUT"
  echo "resolved_targets=$RESOLVED"
  echo "stage1_sources=${SOURCES[*]}"
  for src in "${SOURCES[@]}"; do
    echo "$(basename "$src")_raw_lines=$(wc -l < "$src")"
    echo "$(basename "$src")_valid_lines=$(awk '/^[[:space:]]*$/ { next } /^[A-Za-z0-9_./-]+\.(c|cc|cpp|cxx|h|hh|hpp|hxx):[0-9]+$/ { c++ } END { print c+0 }' "$src")"
  done
  echo "deduped_targets=$(wc -l < "$OUT")"
  echo "sha256=$(sha256sum "$OUT" | awk '{print $1}')"
  echo "resolved_unique=$(awk -F '\t' -v status=unique 'NR>1 && $3==status { c++ } END { print c+0 }' "$RESOLVED")"
  echo "resolved_ambiguous=$(awk -F '\t' -v status=ambiguous 'NR>1 && $3==status { c++ } END { print c+0 }' "$RESOLVED")"
  echo "resolved_missing=$(awk -F '\t' -v status=missing 'NR>1 && $3==status { c++ } END { print c+0 }' "$RESOLVED")"
  echo "mysql_source=$(cd "$MYSQL_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
} | tee "$MANIFEST"
