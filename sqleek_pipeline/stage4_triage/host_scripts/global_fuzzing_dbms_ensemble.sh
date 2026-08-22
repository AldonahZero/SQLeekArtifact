#!/bin/bash

rndname="$1"
dbms="$2"
extraFlag="$3"

if [[ -z $rndname ]]
then
    echo "set \$1 as the rndname."
    exit 1
fi

if [[ -z $dbms ]]
then
    echo "set \$2 as the dbms."
    exit 1
fi

if [[ -z $extraFlag ]]
then
    echo "set \$3 as the extra flag."
    exit 1
fi

SCRIPT_DIR=$(dirname -- "$(readlink -f -- "$0")")
start_script="$SCRIPT_DIR"/../../common/host_scripts/fuzz_dbms_with_specific_seeds.sh
seeds_parent_dir="$SCRIPT_DIR"/../../metadata_collector/
seed_dir="$seeds_parent_dir"/input-set/officials/official_"$dbms"_no_comment/

for ((x=1;x<=1;++x))
do
    "$start_script" \
    --dbms "$dbms"  \
    --input "$seed_dir" \
    $extraFlag \
    --rndname ensemble_griffin_"$rndname"_"$x" \
    --docker_params " \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seed_dir" \
    $extraFlag \
    --rndname ensemble_squirrelSqlite_"$rndname"_"$x" \
    --docker_params " \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e AFL_CUSTOM_MUTATOR_LIBRARY=/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_sqlite.so \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seed_dir" \
    $extraFlag \
    --rndname ensemble_squirrelMysql_"$rndname"_"$x" \
    --docker_params " \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e AFL_CUSTOM_MUTATOR_LIBRARY=/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seed_dir" \
    $extraFlag \
    --rndname ensemble_squirrelPostgres_"$rndname"_"$x" \
    --docker_params " \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e AFL_CUSTOM_MUTATOR_LIBRARY=/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_postgres.so \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seed_dir" \
    $extraFlag \
    --rndname ensemble_griffinAndSquirrel_"$rndname"_"$x" \
    --docker_params " \
    -e NO_AFL_SHUFFLE_QUEUE=1 \
    -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
    -e AFL_CUSTOM_MUTATOR_LIBRARY=/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_sqlite.so:/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so:/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_postgres.so:/workspace/bld_griffin_dynamic/custom_mutator/libmerge_odbc_ver_dynamic.so \
    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1"

done