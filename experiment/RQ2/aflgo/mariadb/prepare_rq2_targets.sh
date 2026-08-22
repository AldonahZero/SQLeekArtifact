#!/usr/bin/env bash
set -e

AFLGO_DIR=${AFLGO_DIR:-/root/SQLeek/experiment/RQ2/aflgo}
MARIADB_SRC=${MARIADB_SRC:-/root/SQLeek/sources/mariadb}
WORK_DIR=${WORK_DIR:-$AFLGO_DIR/mariadb}
STAGE1_TARGET_DIR=${STAGE1_TARGET_DIR:-/root/SQLeek/sqleek_pipeline/stage1_static/output/targets}
OUT_DIR=$WORK_DIR/targets
OUT=$OUT_DIR/mariadb_rq2_targets.txt
RESOLVED=$OUT_DIR/mariadb_rq2_targets_resolved.tsv
MANIFEST=$OUT_DIR/mariadb_rq2_targets_manifest.txt
mkdir -p "$OUT_DIR"

SOURCES=(
  "$STAGE1_TARGET_DIR/mariadb_memory.txt"
  "$STAGE1_TARGET_DIR/mariadb_stale.txt"
  "$STAGE1_TARGET_DIR/mariadb_logic.txt"
)
EXISTING=()
for src in "${SOURCES[@]}"; do
  [ -f "$src" ] && EXISTING+=("$src")
done
if [ "${#EXISTING[@]}" -eq 0 ]; then
  echo "missing all MariaDB Stage 1 target files under $STAGE1_TARGET_DIR" >&2
  exit 1
fi

python3 - "$MARIADB_SRC" "$OUT" "$RESOLVED" "${EXISTING[@]}" <<'PY'
from __future__ import annotations
import sys
from collections import defaultdict
from pathlib import Path

src_root = Path(sys.argv[1])
out = Path(sys.argv[2])
resolved = Path(sys.argv[3])
sources = [Path(x) for x in sys.argv[4:]]
valid_suffixes = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hh', '.hpp', '.hxx'}
by_name: dict[str, list[Path]] = defaultdict(list)
for path in src_root.rglob('*'):
    if '.git' in path.parts:
        continue
    if path.is_file() and path.suffix in valid_suffixes:
        by_name[path.name].append(path)

targets: set[str] = set()
rows: list[tuple[str, str, str, str]] = []
for source in sources:
    for raw in source.read_text(errors='replace').splitlines():
        text = raw.strip()
        if not text or ':' not in text:
            continue
        file_part, line_part = text.rsplit(':', 1)
        file_part = file_part.strip()
        line_part = line_part.strip()
        if not line_part.isdigit():
            continue
        name = Path(file_part).name
        if Path(name).suffix not in valid_suffixes:
            continue
        target = f'{name}:{int(line_part)}'
        targets.add(target)
        matches = by_name.get(name, [])
        if len(matches) == 1:
            status = 'unique'
            match = str(matches[0].relative_to(src_root))
        elif len(matches) > 1:
            status = 'ambiguous'
            match = ';'.join(str(p.relative_to(src_root)) for p in matches[:12])
        else:
            status = 'missing'
            match = ''
        rows.append((source.name, text, status, match))

out.write_text('\n'.join(sorted(targets)) + ('\n' if targets else ''))
with resolved.open('w') as fp:
    fp.write('stage1_file\traw_target\tstatus\tmatches\n')
    for row in rows:
        fp.write('\t'.join(row) + '\n')
PY

if [ ! -s "$OUT" ]; then
  echo "no valid MariaDB AFLGo targets generated" >&2
  exit 1
fi

{
  echo "target_list=$OUT"
  echo "resolved_targets=$RESOLVED"
  echo "stage1_sources=${EXISTING[*]}"
  for src in "${SOURCES[@]}"; do
    base=$(basename "$src")
    if [ -f "$src" ]; then
      echo "${base}_raw_lines=$(wc -l < "$src")"
      echo "${base}_valid_lines=$(awk '/^[[:space:]]*$/ { next } /^[A-Za-z0-9_./-]+\.(c|cc|cpp|cxx|h|hh|hpp|hxx):[0-9]+$/ { c++ } END { print c+0 }' "$src")"
    else
      echo "${base}_missing=1"
    fi
  done
  echo "deduped_targets=$(wc -l < "$OUT")"
  echo "sha256=$(sha256sum "$OUT" | awk '{print $1}')"
  echo "resolved_unique=$(awk -F '\t' 'NR>1 && $3=="unique" { c++ } END { print c+0 }' "$RESOLVED")"
  echo "resolved_ambiguous=$(awk -F '\t' 'NR>1 && $3=="ambiguous" { c++ } END { print c+0 }' "$RESOLVED")"
  echo "resolved_missing=$(awk -F '\t' 'NR>1 && $3=="missing" { c++ } END { print c+0 }' "$RESOLVED")"
  echo "mariadb_source=$(cd "$MARIADB_SRC" && git rev-parse HEAD 2>/dev/null || true)"
  echo "aflgo_source=$(cd "$AFLGO_DIR" && git rev-parse HEAD 2>/dev/null || true)"
} | tee "$MANIFEST"
