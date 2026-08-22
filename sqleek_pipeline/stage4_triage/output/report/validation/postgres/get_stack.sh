#!/bin/bash
set -e

echo "[1/4] 启动调试容器..."
docker run --rm -d \
  --name pg_crash_debug \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  --ulimit core=-1 \
  postgres:16

echo "[2/4] 等待 PostgreSQL 启动..."
sleep 5
until docker exec pg_crash_debug pg_isready -h localhost -U postgres 2>/dev/null; do
  sleep 1
done

echo "[3/4] 安装调试工具..."
docker exec pg_crash_debug bash -c "
  apt-get update -qq
  apt-get install -y gdb postgresql-16-dbgsym 2>&1 | grep -E '^(Inst|Err)' | head -20
  ulimit -c unlimited
  mkdir -p /tmp/cores
  chmod 1777 /tmp/cores
  echo '/tmp/cores/core.%p' > /proc/sys/kernel/core_pattern 2>/dev/null || true
"

echo "[4/4] 触发崩溃并抓取调用栈..."
docker exec pg_crash_debug bash -c "
cat > /tmp/trigger.sql << 'SQL'
CREATE TYPE foo AS (a INT, b INT);
BEGIN;
DECLARE c CURSOR FOR SELECT (i, power(2, 30))::foo FROM generate_series(1,10) i;
FETCH c;
ALTER TYPE foo ALTER ATTRIBUTE b TYPE TEXT;
FETCH c;
COMMIT;
SQL

# 触发崩溃
psql -U postgres -f /tmp/trigger.sql 2>&1 || true
sleep 2

# 查找 core 文件
CORE=\$(ls /tmp/cores/core.* 2>/dev/null | head -1)
if [ -n \"\$CORE\" ]; then
  echo '=== CORE FOUND, extracting stack ==='
  gdb -batch \
    -ex 'set pagination off' \
    -ex 'bt full' \
    -ex 'frame 0' \
    -ex 'info locals' \
    \$(which postgres) \"\$CORE\" 2>&1
else
  echo '=== No core file, trying gdb attach on second trigger ==='
  
  # 重启服务
  su postgres -c 'pg_ctl -D /var/lib/postgresql/data restart -w' 2>/dev/null || true
  sleep 3

  # 后台触发，前台 gdb attach
  psql -U postgres -f /tmp/trigger.sql 2>&1 &
  PSQL_PID=\$!
  sleep 0.5

  BACKEND=\$(pgrep -n -x postgres)
  echo \"Attaching to PID: \$BACKEND\"

  gdb -batch \
    -ex 'set pagination off' \
    -ex 'handle SIGSEGV stop print' \
    -ex 'continue' \
    -ex 'bt full' \
    -ex 'frame 0' \
    -ex 'info locals' \
    -ex 'info registers' \
    \$(which postgres) \$BACKEND 2>&1

  wait \$PSQL_PID 2>/dev/null || true
fi
" 2>&1 | tee crash_stack.txt

echo ""
echo "=== 完成，结果保存在 crash_stack.txt ==="
docker stop pg_crash_debug 2>/dev/null || true