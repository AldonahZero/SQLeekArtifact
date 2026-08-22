#!/bin/bash
set -euo pipefail

seed_file="${1:?seed file required}"
port="${2:-55001}"
exe_dir="/root/bin_original/usr/local/bin"
lib_dir="/root/bin_original/usr/local/lib"

export LD_LIBRARY_PATH="$lib_dir:$lib_dir/monetdb5"
printf 'user=monetdb\npassword=monetdb\n' > /root/.monetdb

"$exe_dir/mserver5" --in-memory --set "mapi_port=$port" > "/tmp/mserver_${port}.log" 2>&1 &
server_pid="$!"

cleanup() {
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT

ready=0
for _ in $(seq 1 40); do
    if "$exe_dir/mclient" -p "$port" < /dev/null >/tmp/mclient_probe.log 2>&1; then
        ready=1
        break
    fi
    sleep 1
done

if [[ "$ready" != "1" ]]; then
    echo "mserver5 did not become ready" >&2
    tail -n 80 "/tmp/mserver_${port}.log" >&2 || true
    exit 1
fi

"$exe_dir/mclient" -p "$port" < "$seed_file"
