#!/bin/bash
# Delete other instances of the reset script.
[ -f /tmp/my_script.lock ] && kill -9 $(cat /tmp/my_script.lock) 2>/dev/null; echo $$ > /tmp/my_script.lock

port_to_use="$1"
exe_orig="$2"

if [ -z "$exe_orig" ]
then
    exe_orig=/root/bin_original/usr/local/mysql/bin/mysqld
fi

data_dir=/dev/shm/mysql_dir_"$port_to_use"/
exe_name=my_"$port_to_use"
exe_path=/workspace/fuzzing/"$exe_name"
socket_file=/tmp/mysql_"$port_to_use".socket
log_err_path=/workspace/logs/griffin.log

while killall -9 "$exe_name"
do
    echo "waiting the server shutdown..."
    sleep 0.3
done

rm -rf "$data_dir"
rm -rf "$data_dir"/*
rm -rf "$data_dir"/.*
mkdir -p "$data_dir"
# umount "$data_dir"
# mount -t tmpfs -o size=2G tmpfs "$data_dir"

mkdir -p "$(dirname "$exe_path")"
ln -sf "$exe_orig" "$exe_path"
ln -s /root/bin_original/usr/local/mysql/lib /root/lib

/root/bin_original/usr/local/mysql/scripts/mysql_install_db --auth-root-authentication-method=normal --datadir="$data_dir"
setsid /workspace/binaries/attaching_all_child \
/workspace/binaries/timeout -s 2000000 \
$exe_path --datadir=$data_dir --log-error=$log_err_path --pid-file=fuckpid.pid --disable-log-bin --socket=$socket_file --port=$port_to_use -uroot &

while ! /root/bin_original/usr/local/mysql/bin/mariadb --port="$port_to_use" -h localhost -u root --protocol=tcp < /dev/null
do
    echo "waiting the server start up..."
    sleep 1
done
