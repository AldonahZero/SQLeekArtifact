#!/bin/bash

[[ -n $GRIFFIN_CONTAINER ]] || (echo "the script should run in griffin docker container."; exit 1) || exit 1 
export SQLSIM_POSTGRES_URI="postgres://postgres:mysecretpassword@172.17.0.1:5432/postgres?options=-c%20search_path%3Dmariadb"
psql postgres://postgres:mysecretpassword@172.17.0.1:5432/postgres -c "create schema mariadb;" || true

export LEGO_MAP_SIZE_DEFAULT_VALUE=400000

isql dsnForMutator < /dev/null > /dev/null || true
export LD_PRELOAD=/root/libraries/libmyodbc8a.so
export SQLSIM_AFL_LOOP_COUNT=1000
