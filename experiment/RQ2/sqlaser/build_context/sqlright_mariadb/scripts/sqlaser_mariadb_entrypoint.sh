#!/usr/bin/env bash
set -euo pipefail

ROOT="${SQLEEK_MYSQL_ROOT:-/opt/dbms}"
DATA="$ROOT/data_all/ori_data"
SOCKET="/tmp/sqlaser_mariadb_init.sock"
PIDFILE="/tmp/sqlaser_mariadb_init.pid"
LOGFILE="/tmp/sqlaser_mariadb_init.log"

find_install_db() {
  local candidate
  for candidate in \
    "$ROOT/scripts/mariadb-install-db" \
    "$ROOT/bin/mariadb-install-db" \
    "$ROOT/scripts/mysql_install_db" \
    "$ROOT/bin/mysql_install_db"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

mkdir -p "$ROOT/data_all"
chown -R mysql:mysql "$ROOT/data_all"
if [ ! -d "$DATA/mysql" ]; then
  install_db="$(find_install_db)"
  runuser -u mysql -- "$install_db" \
    --basedir="$ROOT" \
    --datadir="$DATA" \
    --auth-root-authentication-method=normal \
    --skip-test-db \
    >/tmp/sqlaser_mariadb_install.log 2>&1 \
    || runuser -u mysql -- "$install_db" --basedir="$ROOT" --datadir="$DATA" --skip-test-db
fi

cleanup_init() {
  "$ROOT/bin/mysqladmin" --protocol=socket --socket="$SOCKET" -uroot shutdown >/dev/null 2>&1 || true
  if [ -s "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" >/dev/null 2>&1 || true
  fi
  rm -f "$SOCKET" "$PIDFILE" "$LOGFILE"
}
trap cleanup_init EXIT INT TERM

runuser -u mysql -- "$ROOT/bin/mysqld" \
  --basedir="$ROOT" \
  --datadir="$DATA" \
  --socket="$SOCKET" \
  --pid-file="$PIDFILE" \
  --skip-networking \
  --performance_schema=OFF \
  >"$LOGFILE" 2>&1 &

ready=0
for _ in $(seq 1 90); do
  if "$ROOT/bin/mysqladmin" --protocol=socket --socket="$SOCKET" -uroot ping >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || { cat "$LOGFILE" >&2; exit 1; }
"$ROOT/bin/mysql" --protocol=socket --socket="$SOCKET" -uroot \
  -e "CREATE DATABASE IF NOT EXISTS test_init; CREATE DATABASE IF NOT EXISTS test_sqlright1; FLUSH PRIVILEGES;"
cleanup_init
trap - EXIT INT TERM

if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u mysql -- "$@"
fi
exec "$@"
