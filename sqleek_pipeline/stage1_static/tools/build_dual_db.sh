#!/usr/bin/env bash
# Build dual CodeQL databases for PostgreSQL:
#   DB_after  (fixed/current): stage1_static/output/codeql_db/postgres
#   DB_before (vulnerable):    stage1_static/output/codeql_db/postgres_before
set -euo pipefail

ROOT="/root/SQLeek"
STAGE1="$ROOT/sqleek_pipeline/stage1_static"
OUTDIR="$STAGE1/output"

PG_SRC="/tmp/pg_src"
BEFORE_SRC="/tmp/pg_src_before"

DB_AFTER="$OUTDIR/codeql_db/postgres"
DB_BEFORE="$OUTDIR/codeql_db/postgres_before"

LOG="$ROOT/build.log"

echo "[build_dual_db] building dual CodeQL DBs" | tee -a "$LOG"

if [[ ! -d "$PG_SRC/.git" ]]; then
  echo "[build_dual_db] error: $PG_SRC is not a git repo" | tee -a "$LOG"
  exit 1
fi

mkdir -p "$OUTDIR" "$OUTDIR/codeql_db"

# Ensure DB_after exists (Stage 1 may have created it already)
if [[ -d "$DB_AFTER" ]]; then
  echo "[build_dual_db] DB_after exists: $DB_AFTER" | tee -a "$LOG"
else
  echo "[build_dual_db] error: DB_after missing: $DB_AFTER (run build_and_run.sh first)" | tee -a "$LOG"
  exit 1
fi

echo "[build_dual_db] locating fix commit candidate in git history..." | tee -a "$LOG"
FIX_COMMIT="$(
  set +e
  git -C "$PG_SRC" log --all --oneline | \
    grep -iE "rowtype|eeop_row|stale|rowcache|tupdesc|ExprEvalRowtypeCache" 2>/dev/null | \
    head -1 | awk '{print $1}'
  echo
)"
FIX_COMMIT="$(printf '%s' "$FIX_COMMIT" | head -1 | tr -d '[:space:]')"

if [[ -z "${FIX_COMMIT:-}" ]]; then
  echo "[build_dual_db] warning: fix commit not found; duality degraded (DB_before==DB_after)" | tee -a "$LOG"
  python3 - <<'PY'
import json
from pathlib import Path
out = Path("/root/SQLeek/sqleek_pipeline/stage1_static/output/db_paths.json")
paths = {
  "db_before": "/root/SQLeek/sqleek_pipeline/stage1_static/output/codeql_db/postgres",
  "db_after":  "/root/SQLeek/sqleek_pipeline/stage1_static/output/codeql_db/postgres",
  "dual_available": False,
  "note": "fix_commit_not_found"
}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(paths, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("[build_dual_db] wrote", out)
print(paths)
PY
  exit 0
fi

PARENT_COMMIT="${FIX_COMMIT}^"
echo "[build_dual_db] fix commit: $FIX_COMMIT parent: $PARENT_COMMIT" | tee -a "$LOG"

if [[ ! -d "$DB_BEFORE" ]]; then
  # Prepare BEFORE_SRC worktree
  if [[ -d "$BEFORE_SRC/.git" ]] || [[ -d "$BEFORE_SRC" ]]; then
    rm -rf "$BEFORE_SRC"
  fi
  echo "[build_dual_db] creating worktree $BEFORE_SRC @ $PARENT_COMMIT" | tee -a "$LOG"
  if git -C "$PG_SRC" worktree add "$BEFORE_SRC" "$PARENT_COMMIT" 2>&1 | tee -a "$LOG"; then
    :
  else
    echo "[build_dual_db] worktree failed; falling back to clone+checkout" | tee -a "$LOG"
    git clone "$PG_SRC" "$BEFORE_SRC" 2>&1 | tee -a "$LOG"
    git -C "$BEFORE_SRC" checkout "$PARENT_COMMIT" 2>&1 | tee -a "$LOG"
  fi

  # Build CodeQL DB for BEFORE_SRC with traced build
  echo "[build_dual_db] building DB_before: $DB_BEFORE" | tee -a "$LOG"
  timeout 2400 codeql database create "$DB_BEFORE" \
    --language=cpp \
    --source-root="$BEFORE_SRC" \
    --command="bash \"$STAGE1/tools/codeql_pg_build_inner.sh\" \"$BEFORE_SRC\"" \
    --overwrite \
    2>&1 | tee -a "$LOG"
else
  echo "[build_dual_db] DB_before exists: $DB_BEFORE" | tee -a "$LOG"
fi

python3 - <<'PY'
import json
from pathlib import Path

paths = {
  "db_before": "/root/SQLeek/sqleek_pipeline/stage1_static/output/codeql_db/postgres_before",
  "db_after":  "/root/SQLeek/sqleek_pipeline/stage1_static/output/codeql_db/postgres",
  "dual_available": Path("/root/SQLeek/sqleek_pipeline/stage1_static/output/codeql_db/postgres_before").exists()
}
out = Path("/root/SQLeek/sqleek_pipeline/stage1_static/output/db_paths.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(paths, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("[build_dual_db] wrote", out)
print(paths)
PY

