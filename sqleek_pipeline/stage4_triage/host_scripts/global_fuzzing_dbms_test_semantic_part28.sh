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
squirrelDbs="postgres
sqlite
mysql"

#    -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1 \
#    -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
#    -e SQUIRREL_LIMIT_MUTATE_COUNT=1 \
#    -e SQUIRREL_BOTH_MERGE_AND_UNMERGE=1 \
#    -e SQUIRREL_ENTRIE_PARSE_LIMIT=1

    ######################################
    ### test semantic.                 ###
    ######################################

for db in $squirrelDbs; do

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/"$db"/0_10 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Origin0-10_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/"$db"/10_100 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Origin10-100_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/"$db"/100_1000 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Origin100-1000_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/"$db"/1000_inf \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Origin1000-inf_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/openai_"$db"2"$dbms"/0_10 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Transfer0-10_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/openai_"$db"2"$dbms"/10_100 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Transfer10-100_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/openai_"$db"2"$dbms"/100_1000 \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Transfer100-1000_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"

    "$start_script" \
    --dbms "$dbms"  \
    --input "$seeds_parent_dir"/input-set_sizeclassify/openai_"$db"2"$dbms"/1000_inf \
    $extraFlag \
    --rndname griffin_testsemantic_"$db"Transfer1000-inf_"$rndname" \
    --docker_params " -e AFL_BENCH_JUST_ONE=1  -e SQLSIM_LOG_ERROR_MSG_PATH=/dev/shm/errlog -e SQLSIM_FUZZ_COUNT=0"
    
done

## query monetdb:
# for x in (docker ps --format '{{.Names}}' | grep "griffin_.*_\(squirrel\|griffin\)_" | sort); echo -n "$x, "; docker exec -it "$x" bash -c 'a=$(cat /dev/shm/errlog | wc -l); b=$(grep "^\[SQLSIM LOG\] \$" /dev/shm/errlog | wc -l); echo -n "$a : "; echo $b'; end
## query duckdb:
