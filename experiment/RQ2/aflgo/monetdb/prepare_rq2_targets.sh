#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/SQLeek
AD="$ROOT/experiment/RQ2/aflgo/monetdb"
SRC="$ROOT/sources/monetdb"
STAGE="$ROOT/sqleek_pipeline/stage1_static/output/targets"
OUT="$AD/targets/monetdb_rq2_targets.txt"
RES="$AD/targets/monetdb_rq2_targets_resolved.tsv"
MAN="$AD/targets/monetdb_rq2_targets_manifest.txt"
mkdir -p "$AD/targets" "$AD/logs"
python3 - "$STAGE" "$SRC" "$OUT" "$RES" <<'PY'
from pathlib import Path
import sys, re
stage=Path(sys.argv[1]); src=Path(sys.argv[2]); out=Path(sys.argv[3]); res=Path(sys.argv[4])
inputs=[stage/'monetdb_memory.txt', stage/'monetdb_stale.txt', stage/'monetdb_logic.txt']
by_base={}
for p in src.rglob('*'):
    if p.is_file() and p.suffix.lower() in {'.c','.h','.cc','.cpp','.hpp'}:
        by_base.setdefault(p.name, []).append(p.relative_to(src).as_posix())
seen=set(); rows=[]; resolved=[]; missing=[]; ambiguous=[]
pat=re.compile(r'([^:\s]+):(\d+)')
for f in inputs:
    if not f.exists():
        continue
    for raw in f.read_text(errors='ignore').splitlines():
        raw=raw.strip()
        if not raw or raw.startswith('#'):
            continue
        m=pat.search(raw)
        if not m:
            continue
        file=m.group(1).split('/')[-1]
        line=int(m.group(2))
        cands=by_base.get(file, [])
        status='missing'
        chosen=file
        if len(cands)==1:
            chosen=cands[0]
            status='unique'
        elif len(cands)>1:
            # Prefer non-test generated-looking implementation path if a basename collides.
            ranked=sorted(cands, key=lambda x: (('/test' in x or x.startswith('testing/')), ('build' in x), len(x), x))
            chosen=ranked[0]
            status='ambiguous'
            ambiguous.append((raw, chosen, ';'.join(cands)))
        else:
            missing.append(raw)
        target=f'{file}:{line}'
        if target not in seen:
            seen.add(target)
            rows.append(target)
        resolved.append((raw, target, status, chosen, ';'.join(cands)))
out.write_text('\n'.join(rows)+'\n')
with res.open('w') as fp:
    fp.write('raw\ttarget\tstatus\tchosen_path\tcandidates\n')
    for r in resolved:
        fp.write('\t'.join(map(str,r))+'\n')
print(f'total_raw={len(resolved)}')
print(f'unique_targets={len(rows)}')
print(f'resolved_unique={sum(1 for r in resolved if r[2]=="unique")}')
print(f'resolved_ambiguous={sum(1 for r in resolved if r[2]=="ambiguous")}')
print(f'resolved_missing={sum(1 for r in resolved if r[2]=="missing")}')
PY
{
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source_stage_files="
  for f in "$STAGE"/monetdb_memory.txt "$STAGE"/monetdb_stale.txt "$STAGE"/monetdb_logic.txt; do
    if [ -f "$f" ]; then
      printf '  %s lines=%s sha256=%s\n' "$f" "$(grep -cv '^$' "$f")" "$(sha256sum "$f" | awk '{print $1}')"
    else
      printf '  %s missing\n' "$f"
    fi
  done
  echo "target_list=$OUT"
  echo "target_count=$(wc -l < "$OUT" | tr -d ' ')"
  echo "target_sha256=$(sha256sum "$OUT" | awk '{print $1}')"
  echo "monetdb_commit=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "aflgo_commit=$(git -C "$ROOT/experiment/RQ2/aflgo" rev-parse HEAD 2>/dev/null || echo unknown)"
  awk -F'\t' 'NR>1{c[$3]++} END{for (k in c) print "resolved_" k "=" c[k]}' "$RES" | sort
} > "$MAN"
cat "$MAN"
