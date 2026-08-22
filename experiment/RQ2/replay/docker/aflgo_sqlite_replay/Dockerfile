# Lightweight wrapper for the SQLite llvm-cov replay environment used by RQ2 AFLGo.
# The heavy llvm-cov SQLite binary is provided by the existing local image.
FROM griffin_sqlite_llvmcov

ENV RQ2_SQLITE_BINARY=/root/bld_llvmcov/sqlite3 \
    RQ2_SQLITE_AMALGAMATION_IN_IMAGE=/root/bld_llvmcov/sqlite3.c \
    RQ2_SQLITE_LLVM_PROFDATA=llvm-profdata-12 \
    RQ2_SQLITE_LLVM_COV=llvm-cov-12

RUN test -x "$RQ2_SQLITE_BINARY" \
 && test -f "$RQ2_SQLITE_AMALGAMATION_IN_IMAGE" \
 && command -v "$RQ2_SQLITE_LLVM_PROFDATA" \
 && command -v "$RQ2_SQLITE_LLVM_COV"

WORKDIR /rq2
