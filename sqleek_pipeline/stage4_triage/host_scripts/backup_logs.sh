#!/bin/bash
set -e

dbms="$1"

if [[ -z $dbms ]]
then
    echo "Please set \$1 as the dbms name."
    exit 1    
fi

all_containers=$(docker ps -a --format "{{.Names}}")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE4_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPORT_ROOT="$STAGE4_DIR/output/backup_logs"
mkdir -p "$EXPORT_ROOT"

if [[ "$dbms" == sqleek_fuzz_* ]]; then
    pattern="^${dbms}([_0-9].*)?$"
else
    pattern="^sqleek_fuzz_${dbms}([_0-9].*)?$"
fi
containers=$(printf '%s\n' "$all_containers" | grep -E "$pattern" || true)
match_desc="$pattern"

if [[ -z "$containers" ]]; then
    echo "No containers matched: ${match_desc}"
    exit 0
fi

copy_if_exists() {
    local container="$1"
    local src="$2"
    local dst="$3"

    if docker exec "$container" test -e "$src" >/dev/null 2>&1; then
        docker cp "$container":"$src" "$dst"
    else
        echo "Skip missing path in $container: $src"
    fi
}

for x in $containers
do
    out_dir="$EXPORT_ROOT/$x"
    mkdir -p "$out_dir"
    mkdir -p "$out_dir"/fuzzing/fuzz_out_dir/default/

    if docker exec "$x" test -d /workspace/logSaved >/dev/null 2>&1; then
        docker cp "$x":/workspace/logSaved "$out_dir"/
    elif docker exec "$x" test -d /workspace/fuzzing/logSaved >/dev/null 2>&1; then
        docker cp "$x":/workspace/fuzzing/logSaved "$out_dir"/
    else
        echo "Skip missing logSaved in $x"
    fi

    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/crashes   ./"$x"/fuzzing/fuzz_out_dir/default/
    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/plot_data ./"$x"/fuzzing/fuzz_out_dir/default/ 
    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/cmdline   ./"$x"/fuzzing/fuzz_out_dir/default/
    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzz_bitmap ./"$x"/fuzzing/fuzz_out_dir/default/
    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzzer_setup ./"$x"/fuzzing/fuzz_out_dir/default/ 
    # docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzzer_stats ./"$x"/fuzzing/fuzz_out_dir/default/
    copy_if_exists "$x" /workspace/fuzzing/fuzz_out_dir/default "$out_dir"/fuzzing/fuzz_out_dir/
    copy_if_exists "$x" /workspace/fuzzerStatLogging "$out_dir"/

    find "$out_dir" -name core -delete
    echo "Exported $x to $out_dir"
done
