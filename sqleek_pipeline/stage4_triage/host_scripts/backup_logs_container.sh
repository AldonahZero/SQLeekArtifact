#!/bin/bash

x="$1"

if [[ -z $x ]]
then
    echo "Please set \$1 as the container name."
    exit 1    
fi

mkdir -p "$x"
mkdir -p ./"$x"/fuzzing/fuzz_out_dir/default/
docker cp "$x":/workspace/logSaved ./"$x"/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/crashes   ./"$x"/fuzzing/fuzz_out_dir/default/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/plot_data ./"$x"/fuzzing/fuzz_out_dir/default/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/cmdline   ./"$x"/fuzzing/fuzz_out_dir/default/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzz_bitmap ./"$x"/fuzzing/fuzz_out_dir/default/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzzer_setup ./"$x"/fuzzing/fuzz_out_dir/default/
# docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/fuzzer_stats ./"$x"/fuzzing/fuzz_out_dir/default/
docker cp "$x":/workspace/fuzzing/fuzz_out_dir/default/          ./"$x"/fuzzing/fuzz_out_dir/
docker cp "$x":/workspace/fuzzerStatLogging ./"$x"/

rm $(find "$x" -name core)
