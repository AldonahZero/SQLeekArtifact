#!/bin/bash
set -e

echo "[1/3] 启动容器..."
docker run --rm -d \
  --name pg_debug2 \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  postgres:16

sleep 5
until docker exec pg_debug2 pg_isready -h localhost -U postgres 2>/dev/null; do
  sleep 1
done

echo "[2/3] 安装 gdb + 调试符号..."
docker exec pg_debug2 bash -c "
  apt-get update -qq
  apt-get install -y gdb postgresql-16-dbgsym 2>&1 | grep '^Inst' | head -10
"

echo "[3/3] 用 gdb 单用户模式抓栈..."
docker exec pg_debug2 bash -c "
# 停掉现有服务，改用单用户模式
su postgres -c 'pg_ctl -D /var/lib/postgresql/data stop -m fast' 2>/dev/null || true
sleep 2

# 写 SQL 输入文件（单用户模式从 stdin 读）
cat > /tmp/trigger_single.sql << 'EOF'
CREATE TYPE foo AS (a INT, b INT);
BEGIN;
DECLARE c CURSOR FOR SELECT (i, power(2, 30))::foo FROM generate_series(1,10) i;
FETCH c;
ALTER TYPE foo ALTER ATTRIBUTE b TYPE TEXT;
FETCH c;
COMMIT;
EOF

# 用 gdb 运行单用户模式 postgres，SQL 从 stdin 输入
su postgres -c \"
  gdb -batch \
    -ex 'set pagination off' \
    -ex 'handle SIGSEGV stop print nopass' \
    -ex 'run --single -j -D /var/lib/postgresql/data postgres < /tmp/trigger_single.sql' \
    -ex 'bt full' \
    -ex 'frame 0' \
    -ex 'info locals' \
    -ex 'info registers rip rbp rsp' \
    \$(which postgres) 2>&1
\"
" 2>&1 | tee crash_stack2.txt

echo ""
echo "=== 结果保存在 crash_stack2.txt ==="
docker stop pg_debug2 2>/dev/null || true