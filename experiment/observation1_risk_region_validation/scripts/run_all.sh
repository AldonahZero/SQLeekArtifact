#!/usr/bin/env bash
# Reproduce Observation 1 risk-region validation.
set -euo pipefail

ROOT=${SQLEEK_ROOT:-/root/SQLeek}
EXP="$ROOT/experiment/observation1_risk_region_validation"
SCRIPTS="$EXP/scripts"
LOG_DIR="$EXP/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run_all_$(date -u +%Y%m%d_%H%M%S).log"
COMMAND_LOG="$LOG_DIR/commands.log"

run_step() {
  echo "[$(date -Is)] $*" | tee -a "$LOG" "$COMMAND_LOG"
  nice -n "${SQLEEK_OBS1_NICE:-10}" "$@" 2>&1 | tee -a "$LOG"
}

echo "Observation 1 experiment: $EXP" | tee -a "$LOG"
echo "SQLEEK_ROOT=$ROOT" | tee -a "$LOG"

run_step python3 "$SCRIPTS/discover_environment.py" --root "$ROOT"

for dbms in mysql postgresql; do
  run_step python3 "$SCRIPTS/extract_git_history.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/identify_bug_fixes.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/build_risk_regions.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/map_bug_fixes_to_regions.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/load_official_coverage.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/load_sql_reachability.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/build_risk_region_dataset.py" --dbms "$dbms"
  run_step python3 "$SCRIPTS/run_statistical_analysis.py" --dbms "$dbms"
done

run_step python3 "$SCRIPTS/run_statistical_analysis.py" --dbms combined
run_step python3 "$SCRIPTS/generate_report.py"

echo "Done. Reports: $EXP/reports" | tee -a "$LOG"
