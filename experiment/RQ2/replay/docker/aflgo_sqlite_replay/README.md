# AFLGo SQLite RQ2 Replay Environment

This directory records the lightweight Docker wrapper for the SQLite llvm-cov replay image used by the AFLGo SQLite RQ2 replay.

The actual replay driver is:

```bash
cd /root/SQLeek/experiment/RQ2
QUEUE_DIR=/root/SQLeek/experiment/RQ2/aflgo/sqlite/output_rq2_24h/sqlite_aflgo_r1_20260707_063357/sqlite_aflgo_r1/queue \
RUN_ID=sqlite_aflgo_r1_live \
REPEAT_ID=1 \
TOOL=AFLGo \
DBMS=sqlite \
VERSION=3.53.2 \
RQ2_SQLITE_IMAGE=griffin_sqlite_llvmcov \
RQ2_SQLITE_BINARY=/root/bld_llvmcov/sqlite3 \
RQ2_SQLITE_AMALGAMATION_IN_IMAGE=/root/bld_llvmcov/sqlite3.c \
bash replay/rq2_replay_aflgo_sqlite_full.sh
```

The large `*.cov.json`, `*.profdata`, and temporary queue hardlink workspace are reproducible intermediates and are not retained after summary CSVs have been copied into `result/audit/aflgo_sqlite` and `result/data`.
