#!/bin/bash

tempseedsdir="$(mktemp -d)"
image=""


dbms=""
rndname=""
cmin=""
container=""
docker_params=""
fuzz_dir_volume_path=""
fuzz_instance_name=""


abexit() {
    rm -rf "$tempseedsdir"
    exit 1
}

usage() {
    echo "Usage: $(basename "$0") --dbms mysql|postgres|... --rndname x --input dir1 [--input dir2 ...] [--docker_params '...'] [--cmin]"
}

# SCRIPT_DIR=$(dirname -- "$(readlink -f -- "$0")")

options=$(getopt -o "" --long docker_params:,dbms:,rndname:,input:,cmin -- "$@")
eval set -- "$options"

while true; do
    case "$1" in
    --dbms)
        dbms="$2"
        image="griffin_$dbms"
        shift 2
        ;;
    --docker_params)
        docker_params="$2"
        shift 2
        ;;
    --rndname)
        rndname="$2"
        container="$image"_"$rndname"
        shift 2
        ;;
    --input)
        cp -r "$2"/ "$tempseedsdir"
        shift 2
        ;;
    --cmin)
        cmin=1
        shift 1
        ;;
    --)
        shift
        break
        ;;
    *)
        echo "Unknown option: $1"
        usage
        abexit
        ;;
    esac
done

if [[ -z "$rndname" || -z "$(ls "$tempseedsdir")" || -z "$dbms" ]]
then
    usage
    abexit
fi

echo "rndname   =    $rndname"
echo "seeds     =    $(ls "$tempseedsdir")"
echo "tmpdir    =    $tempseedsdir"
echo "image     =    $image"
echo "container =    $container"
echo "params    =    $docker_params"

if [[ -n "$cmin" ]]
then
    echo "CMIN mode."
else
    echo "DISABLED cmin mode."
fi
echo "Press any key to continue."
read -r

eval "docker run --privileged -itd -m 70G --cpus=10 --shm-size=5G $docker_params --name $container $image" || { echo "container $container already exists! Stopped."; abexit; }
docker exec "$container" rm -rf /workspace/seeds

if [[ -n "$cmin" ]]
then
    docker cp "$tempseedsdir"/ "$container":/workspace/seeds_before_min
    docker exec "$container" bash -c "
source /workspace/scripts/base_env.sh
unset AFL_CUSTOM_MUTATOR_LIBRARY
AFL_BENCH_JUST_ONE=1 /workspace/binaries/AFLplusplus_modified/afl-fuzz -i /workspace/seeds_before_min/ -o \$GRIFFIN_OUTPUT_PATH -S seeds -- /workspace/bld_griffin/autodriver_odbc_v5_aflpp NULL
"
    docker exec "$container" bash -c "
mkdir /workspace/seeds
echo 'z' > /workspace/seeds/z
"
    docker exec "$container" /workspace/scripts/start_all.sh
else
    docker cp "$tempseedsdir"/ "$container":/workspace/seeds
    docker exec "$container" /workspace/scripts/start_all.sh
fi

rm -rf "$tempseedsdir"
