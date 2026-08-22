#!/bin/bash
set -e
set -o pipefail

CONTAINER=pg_debug4
IMAGE=sqleek-pg18-debug:18.3
PG_VERSION=18.3
PG_PREFIX=/usr/local/pg18
PGDATA=/tmp/pgdata
PORT=55432

cleanup() {
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[1/3] 构建调试镜像 ${IMAGE}（首次运行需要几分钟）..."
  docker build \
    -f Dockerfile.pg18-debug \
    --build-arg PG_VERSION="$PG_VERSION" \
    --build-arg PG_PREFIX="$PG_PREFIX" \
    -t "$IMAGE" .
else
  echo "[1/3] 使用已有调试镜像 ${IMAGE}"
fi

echo "[2/3] 启动容器并初始化数据库..."
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run --rm -d \
  --name "$CONTAINER" \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  "$IMAGE"

docker exec "$CONTAINER" bash -c "
  set -e
  rm -rf ${PGDATA}
  mkdir -p ${PGDATA}
  chown pguser ${PGDATA}
  su pguser -c '${PG_PREFIX}/bin/initdb -D ${PGDATA}' 2>&1 | tail -8
  echo '[+] initdb done'
"

echo "[3/3] 触发崩溃，抓取调用栈..."
docker exec "$CONTAINER" bash -c "
  set -e
  export PATH=${PG_PREFIX}/bin:\$PATH

  su pguser -c '${PG_PREFIX}/bin/pg_ctl -D ${PGDATA} -l /tmp/postgres.log -o \"-h 127.0.0.1 -p ${PORT}\" start -w'

  cat > /tmp/trigger.sql <<'SQL'
\set ON_ERROR_STOP on
DROP TYPE IF EXISTS foo CASCADE;
CREATE TYPE foo AS (a INT, b INT);
BEGIN;
DECLARE c CURSOR FOR SELECT (i, power(2, 30))::foo FROM generate_series(1,10) i;
FETCH c;
ALTER TYPE foo ALTER ATTRIBUTE b TYPE TEXT;
\pset tuples_only on
\pset format unaligned
\o /tmp/backend_pid
SELECT pg_backend_pid();
\o
SELECT pg_sleep(20);
FETCH c;
COMMIT;
SQL
  chown pguser /tmp/trigger.sql
  rm -f /tmp/backend_pid /tmp/psql.out /tmp/gdb.out

  su pguser -c '${PG_PREFIX}/bin/psql -h 127.0.0.1 -p ${PORT} -d postgres -f /tmp/trigger.sql' > /tmp/psql.out 2>&1 &
  PSQL_PID=\$!

  for i in \$(seq 1 100); do
    if [ -s /tmp/backend_pid ]; then
      break
    fi
    sleep 0.1
  done

  if [ ! -s /tmp/backend_pid ]; then
    echo '=== backend pid was not captured ==='
    cat /tmp/psql.out || true
    exit 1
  fi

  BACKEND=\$(tr -dc '0-9' < /tmp/backend_pid)
  echo \"=== Attaching gdb to backend PID: \$BACKEND ===\"

  gdb -batch \
    -ex 'set pagination off' \
    -ex 'handle SIGSEGV stop print nopass' \
    -ex 'continue' \
    -ex 'bt full' \
    -ex 'frame 0' \
    -ex 'info locals' \
    -ex 'info registers rip rbp rsp' \
    ${PG_PREFIX}/bin/postgres \"\$BACKEND\" > /tmp/gdb.out 2>&1 || true

  wait \$PSQL_PID 2>/dev/null || true

  echo '=== psql output ==='
  cat /tmp/psql.out || true
  echo '=== postgres log tail ==='
  tail -80 /tmp/postgres.log || true
  echo '=== gdb output ==='
  cat /tmp/gdb.out || true
" 2>&1 | tee crash_stack4.txt

echo ""
echo "=== 完成，结果保存在 crash_stack4.txt ==="
echo "=== 前50行预览 ==="
head -50 crash_stack4.txt