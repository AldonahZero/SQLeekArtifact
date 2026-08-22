#!/bin/bash

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <process-name> <container-name>"
    exit 1
fi

process_name=$1
container_name=$2

# Get the container's PID namespace
container_pid_namespace=$(docker inspect --format '{{.State.Pid}}' "$container_name")

if [[ -z "$container_pid_namespace" ]]; then
    # echo "Failed to get the main PID (namespace) for container: $container_name"
    exit 1
fi

# Check all PIDs in that namespace
for pid in $(sudo ls -1 /proc/$container_pid_namespace/task/); do
    current_process_name=$(sudo cat /proc/$container_pid_namespace/task/$pid/comm 2>/dev/null)
    if [[ "$current_process_name" == "$process_name" ]]; then
        # echo "Found process. PID in host: $pid"
        echo "$pid"
        exit 0
    fi
done

# echo "No process named $process_name found in container $container_name"
exit 1