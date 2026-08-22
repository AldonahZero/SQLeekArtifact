#!/usr/bin/env bash
# Generate the Stage 1 PGQS Query A for one DBMS without running CodeQL analyze.
set -euo pipefail

ROOT=/root/SQLeek
STAGE1="$ROOT/sqleek_pipeline/stage1_static"
DBMS="${SQLEEK_DBMS:-postgres}"
TOP=150

usage() {
  cat <<'EOF'
Usage: generate_pgqs.sh [--dbms postgres|mysql|mariadb|sqlite|monetdb] [--top N]

Generates:
  postgres -> queries/pgqs.ql
  mysql    -> queries/mysql_pgqs.ql
  mariadb  -> queries/mariadb_pgqs.ql
  sqlite   -> queries/sqlite_pgqs.ql
  monetdb  -> queries/monetdb_pgqs.ql
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbms)
      [[ $# -ge 2 ]] || { echo "[generate_pgqs] --dbms requires a value" >&2; exit 1; }
      DBMS="$2"
      shift 2
      ;;
    --top)
      [[ $# -ge 2 ]] || { echo "[generate_pgqs] --top requires a value" >&2; exit 1; }
      TOP="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[generate_pgqs] unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$DBMS" in
  postgres|mysql|mariadb|sqlite|monetdb)
    ;;
  *)
    echo "[generate_pgqs] unsupported DBMS: $DBMS" >&2
    exit 1
    ;;
esac

echo "[generate_pgqs] dbms=$DBMS top=$TOP"
python3 "$STAGE1/tools/gen_priority_qll.py" --dbms "$DBMS" --top "$TOP"

if [[ "$DBMS" == "postgres" ]]; then
  python3 "$STAGE1/tools/extract_patch_features.py"
else
  echo "[generate_pgqs] skip extract_patch_features.py for $DBMS"
fi

SQLEEK_DBMS="$DBMS" python3 "$STAGE1/tools/pgqs.py" --dbms "$DBMS"
