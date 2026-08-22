#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SRC_CODE_DIR="$SCRIPT_DIR"/../../../

container_name="$1"

if [[ -z $container_name ]]; then
  echo "Please set \$1 as the container name."
fi

docker exec -it "$container_name" mkdir -p /workspace/source/

for path in aflpp_dependencies autodriver_odbc custom_mutator include src extensions CMakeLists.txt scripts; do
  full_path="$SRC_CODE_DIR"/"$path"
  docker cp "$full_path" "$container_name":/workspace/source/
done