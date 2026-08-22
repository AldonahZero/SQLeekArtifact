#!/usr/bin/env bash
# Stage 1: CodeQL static analysis for one DBMS.
# PostgreSQL uses the full PGQS flow; MySQL/MariaDB/SQLite/MonetDB use the common
# dbms_*.ql queries and DBMS-prefixed outputs.
# Logs: sqleek_pipeline/stage1_static/output/build.log
set -euo pipefail

ROOT=/root/SQLeek
PIPE="$ROOT/sqleek_pipeline"
STAGE1="$PIPE/stage1_static"
LOG="$STAGE1/output/build.log"
DBMS="${SQLEEK_DBMS:-postgres}"

mkdir -p "$STAGE1/output" "$STAGE1/output/codeql_db"
touch "$LOG"

# CodeQL 2.25+ bundles packs under /root/codeql/qlpacks — required for sqleek/stage1-dbms-queries
export PATH="/root/codeql:${PATH:-}"
export CODEQL_ALLOW_INSTALLATION_ANYWHERE="${CODEQL_ALLOW_INSTALLATION_ANYWHERE:-true}"
CODEQL_SEARCH=(--search-path=/root/codeql/qlpacks)

log() {
  local line
  line="$(date -Is) $*"
  printf '%s\n' "$line" | tee -a "$LOG"
}

die() {
  log "[!] $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: build_and_run.sh [--dbms postgres|mysql|mariadb|sqlite|monetdb]

Environment overrides:
  SQLEEK_DBMS          Default DBMS when --dbms is omitted.
  SQLEEK_POSTGRES_SRC  PostgreSQL source root (default: /tmp/pg_src).
  SQLEEK_MYSQL_SRC     MySQL source root (default: /root/SQLeek/sources/mysql).
  SQLEEK_MARIADB_SRC   MariaDB source root (default: /root/SQLeek/sources/mariadb).
  SQLEEK_SQLITE_SRC    SQLite source root (default: /root/SQLeek/sources/sqlite).
  SQLEEK_MONETDB_SRC   MonetDB source root (default: /root/SQLeek/sources/monetdb).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbms)
      [[ $# -ge 2 ]] || die "--dbms requires a value"
      DBMS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$DBMS" in
  postgres)
    SRC="${SQLEEK_POSTGRES_SRC:-/tmp/pg_src}"
    ;;
  mysql)
    SRC="${SQLEEK_MYSQL_SRC:-$ROOT/sources/mysql}"
    ;;
  mariadb)
    SRC="${SQLEEK_MARIADB_SRC:-$ROOT/sources/mariadb}"
    ;;
  sqlite)
    SRC="${SQLEEK_SQLITE_SRC:-$ROOT/sources/sqlite}"
    ;;
  monetdb)
    SRC="${SQLEEK_MONETDB_SRC:-$ROOT/sources/monetdb}"
    ;;
  *)
    die "unsupported DBMS: $DBMS"
    ;;
esac

# CodeQL DB + CSV 均在 stage1_static/output/（不再使用仓库根下 codeql_results/）
DB="$STAGE1/output/codeql_db/$DBMS"
OUT="$STAGE1/output/codeql_results/$DBMS"
mkdir -p "$OUT"

log "=== [Stage 1] build_and_run.sh dbms=$DBMS ==="

if [[ "$DBMS" == "postgres" ]]; then
  QUERY_A_PGQS="$STAGE1/queries/pgqs.ql"
else
  QUERY_A_PGQS="$STAGE1/queries/${DBMS}_pgqs.ql"
fi
QUERY_A_MANUAL="$STAGE1/queries/dbms_stale_descriptor.ql"
if [[ "$DBMS" == "postgres" ]]; then
  MEMORY_QUERY="$STAGE1/queries/dbms_memory_sinks.ql"
else
  MEMORY_QUERY="$STAGE1/queries/${DBMS}_memory_sinks.ql"
fi

if [[ "$DBMS" == "postgres" ]]; then
  log "[1/7] Extract(Δ): patch feature vector"
  python3 "$STAGE1/tools/extract_patch_features.py" 2>&1 | tee -a "$LOG"
  [[ ${PIPESTATUS[0]} -eq 0 ]] || die "extract_patch_features.py failed"
else
  log "[1/7] Extract(Δ): skipped for $DBMS (PGQS is PostgreSQL-specific)"
fi

python3 "$STAGE1/tools/gen_priority_qll.py" --dbms "$DBMS" --top 150 2>&1 | tee -a "$LOG"
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "gen_priority_qll.py failed"

if [[ "$DBMS" == "postgres" ]]; then
  log "[2/7] Dual DB builder (optional): db_paths.json"
  bash "$STAGE1/tools/build_dual_db.sh" 2>&1 | tee -a "$LOG"
  [[ ${PIPESTATUS[0]} -eq 0 ]] || die "build_dual_db.sh failed"

  log "[3/7] PGQS: synthesize Query A ($(basename "$QUERY_A_PGQS"))"
  python3 "$STAGE1/tools/pgqs.py" 2>&1 | tee -a "$LOG"
  [[ ${PIPESTATUS[0]} -eq 0 ]] || die "pgqs.py failed"
else
  log "[2/7] Dual DB builder: skipped for $DBMS"
  log "[3/7] PGQS: synthesize DBMS Query A ($(basename "$QUERY_A_PGQS"))"
  SQLEEK_DBMS="$DBMS" python3 "$STAGE1/tools/pgqs.py" --dbms "$DBMS" 2>&1 | tee -a "$LOG"
  [[ ${PIPESTATUS[0]} -eq 0 ]] || die "pgqs.py failed for $DBMS"
fi

if [[ ! -d "$DB" ]]; then
  log "[*] CodeQL DB not found; creating under $DB (may take 10–30+ min)"
  if [[ ! -d "$SRC" ]]; then
    die "$DBMS source missing at $SRC"
  fi
  if [[ "$DBMS" == "postgres" ]]; then
    if [[ ! -d "$SRC/.git" ]] || [[ ! -f "$SRC/configure" ]]; then
      die "PostgreSQL source missing or not bootstrapped at $SRC (need .git and ./configure) — cannot create CodeQL DB"
    fi
    log "[*] traced build: tools/codeql_pg_build_inner.sh (make clean unless SQLEEK_CODEQL_SKIP_MAKE_CLEAN=1)"
    (
      timeout 2400 codeql database create "$DB" \
        --language=cpp \
        --source-root="$SRC" \
        --command="bash \"$STAGE1/tools/codeql_pg_build_inner.sh\" \"$SRC\"" \
        --overwrite
    ) 2>&1 | tee -a "$LOG"
  else
    log "[*] no-build CodeQL extraction for $DBMS source: $SRC"
    (
      timeout 2400 codeql database create "$DB" \
        --language=cpp \
        --source-root="$SRC" \
        --build-mode=none \
        --overwrite
    ) 2>&1 | tee -a "$LOG"
  fi
  if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    die "codeql database create failed or timed out — see log"
  fi
fi

[[ -d "$DB" ]] || die "No CodeQL DB at $DB"

log "[4/7] CodeQL analyze: Query A (manual + PGQS) + B/C"

# Query A (manual): PostgreSQL merges manual + PGQS; other DBMSes keep manual only.
log "[*] codeql database analyze: dbms_stale_descriptor (manual)"
codeql database analyze "${CODEQL_SEARCH[@]}" \
  --format=csv \
  --output="$OUT/dbms_stale_descriptor_manual.csv" \
  --rerun \
  "$DB" \
  "$QUERY_A_MANUAL" \
  2>&1 | tee -a "$LOG"
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  die "codeql database analyze failed for dbms_stale_descriptor (manual) — see log"
fi

if [[ "$DBMS" == "postgres" ]]; then
  QUERY_A_PGQS_CSV="$OUT/dbms_stale_descriptor_pgqs.csv"
else
  QUERY_A_PGQS_CSV="$OUT/${DBMS}_pgqs.csv"
fi

# Query A (PGQS / DBMS-specific): write to a DBMS-specific CSV for non-Postgres.
log "[*] codeql database analyze: $(basename "$QUERY_A_PGQS" .ql)"
codeql database analyze "${CODEQL_SEARCH[@]}" \
  --format=csv \
  --output="$QUERY_A_PGQS_CSV" \
  --rerun \
  "$DB" \
  "$QUERY_A_PGQS" \
  2>&1 | tee -a "$LOG"
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  die "codeql database analyze failed for $(basename "$QUERY_A_PGQS") — see log"
fi

# Merge Query A outputs for downstream parsing.
cat "$OUT/dbms_stale_descriptor_manual.csv" "$QUERY_A_PGQS_CSV" > "$OUT/dbms_stale_descriptor.csv"
if [[ "$DBMS" == "postgres" ]]; then
  [[ -s "$OUT/dbms_stale_descriptor.csv" ]] || die "merged Query A CSV is empty: $OUT/dbms_stale_descriptor.csv"
elif [[ ! -s "$OUT/dbms_stale_descriptor.csv" ]]; then
  log "[*] $DBMS Query A produced no stale-descriptor rows; continuing with memory/callchain queries"
fi

log "[*] codeql database analyze: $(basename "$MEMORY_QUERY" .ql) -> dbms_memory_sinks.csv"
codeql database analyze "${CODEQL_SEARCH[@]}" \
  --format=csv \
  --output="$OUT/dbms_memory_sinks.csv" \
  --rerun \
  "$DB" \
  "$MEMORY_QUERY" \
  2>&1 | tee -a "$LOG"
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  die "codeql database analyze failed for $(basename "$MEMORY_QUERY") — see log"
fi
if [[ "$DBMS" == "postgres" ]]; then
  [[ -s "$OUT/dbms_memory_sinks.csv" ]] || die "analyze produced empty or missing CSV: $OUT/dbms_memory_sinks.csv"
elif [[ ! -s "$OUT/dbms_memory_sinks.csv" ]]; then
  log "[*] $DBMS memory sink query produced no rows; parse_targets may use configured fallbacks"
fi

q=dbms_callchain
log "[*] codeql database analyze: $q"
codeql database analyze "${CODEQL_SEARCH[@]}" \
  --format=csv \
  --output="$OUT/${q}.csv" \
  --rerun \
  "$DB" \
  "$STAGE1/queries/${q}.ql" \
  2>&1 | tee -a "$LOG"
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  die "codeql database analyze failed for $q — see log"
fi
if [[ "$DBMS" == "postgres" ]]; then
  [[ -s "$OUT/${q}.csv" ]] || die "analyze produced empty or missing CSV: $OUT/${q}.csv"
elif [[ ! -s "$OUT/${q}.csv" ]]; then
  log "[*] $DBMS $q produced no rows; parse_targets may use configured fallbacks"
fi

log "[5/7] parse_targets: CSV → targets/"
python3 "$STAGE1/parse_targets.py" --dbms "$DBMS" 2>&1 | tee -a "$LOG"
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "parse_targets.py failed"
log "[6/7] gen_phi_mapping: callchains → Φ mapping"
SQLEEK_DBMS="$DBMS" python3 "$STAGE1/gen_phi_mapping.py" 2>&1 | tee -a "$LOG"
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "gen_phi_mapping.py failed"

if [[ "$DBMS" == "postgres" ]]; then
  log "[7/7] validate_pgqs (paper metrics)"
  python3 "$STAGE1/tools/validate_pgqs.py" 2>&1 | tee -a "$LOG" || true
else
  log "[7/7] validate_pgqs: skipped for $DBMS"
fi

log "=== [Stage 1] build_and_run.sh dbms=$DBMS done ==="
