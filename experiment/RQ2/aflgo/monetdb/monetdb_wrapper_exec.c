#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    const char *script = getenv("AFLGO_MONETDB_SCRIPT");
    if (!script || !script[0]) {
        script = "/root/SQLeek/experiment/RQ2/aflgo/monetdb/monetdb_single_wrapper.sh";
    }
    if (argc < 2) {
        fprintf(stderr, "usage: %s <sql-file>\n", argv[0]);
        return 2;
    }
    execl("/bin/bash", "bash", script, argv[1], (char *)NULL);
    perror("execl");
    return 127;
}
