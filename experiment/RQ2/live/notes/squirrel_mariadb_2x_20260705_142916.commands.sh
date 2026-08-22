#!/usr/bin/env bash
set -euo pipefail

# repeat r1
docker run --privileged -itd -m 70G --cpus=10 --shm-size=5G \
  -e GRIFFIN_CONTAINER=1 \
  -e AFL_CUSTOM_MUTATOR_LIBRARY="/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so" \
  -e SQUIRREL_DISABLE_MERGE=1 \
  --name "rq2_squirrel_mariadb_r1_20260705_142916" "griffin_mariadb_llvmcov:latest"
docker exec "rq2_squirrel_mariadb_r1_20260705_142916" rm -rf /workspace/seeds
docker exec "rq2_squirrel_mariadb_r1_20260705_142916" mkdir -p /workspace/seeds
docker cp "/root/dfuzz-griffin/docker/metadata_collector/input-set/input-set_for_squirrel/official_mariadb"/. "rq2_squirrel_mariadb_r1_20260705_142916":/workspace/seeds/
docker exec "rq2_squirrel_mariadb_r1_20260705_142916" /workspace/scripts/start_all.sh

# repeat r2
docker run --privileged -itd -m 70G --cpus=10 --shm-size=5G \
  -e GRIFFIN_CONTAINER=1 \
  -e AFL_CUSTOM_MUTATOR_LIBRARY="/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so" \
  -e SQUIRREL_DISABLE_MERGE=1 \
  --name "rq2_squirrel_mariadb_r2_20260705_142916" "griffin_mariadb_llvmcov:latest"
docker exec "rq2_squirrel_mariadb_r2_20260705_142916" rm -rf /workspace/seeds
docker exec "rq2_squirrel_mariadb_r2_20260705_142916" mkdir -p /workspace/seeds
docker cp "/root/dfuzz-griffin/docker/metadata_collector/input-set/input-set_for_squirrel/official_mariadb"/. "rq2_squirrel_mariadb_r2_20260705_142916":/workspace/seeds/
docker exec "rq2_squirrel_mariadb_r2_20260705_142916" /workspace/scripts/start_all.sh

