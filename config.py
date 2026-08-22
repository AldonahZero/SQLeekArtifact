import os
from pathlib import Path


# Honor SQLEEK_ROOT when the pipeline is launched in the experiment container;
# otherwise resolve paths relative to this checkout for local reproduction.
ROOT           = Path(os.environ.get("SQLEEK_ROOT", Path(__file__).resolve().parent)).expanduser().resolve()
PIPELINE_DIR   = ROOT / "sqleek_pipeline"
# Stage 1 artifacts (callchains.json, phi_mapping.json, *_memory.txt, weights.json)
TARGETS_DIR    = PIPELINE_DIR / "stage1_static" / "output" / "targets"
# CodeQL PostgreSQL DB + CSV (build_and_run.sh)
CODEQL_DB      = PIPELINE_DIR / "stage1_static" / "output" / "codeql_db" / "postgres"
SEEDS_DIR      = PIPELINE_DIR / "stage2_setup" / "output" / "seeds"
# Stage 3 owns fuzz execution outputs (queue/crashes/deferred).
OUTPUT_DIR     = PIPELINE_DIR / "stage3_scheduler" / "output" / "fuzz"
REPORT_DIR     = PIPELINE_DIR / "stage4_triage" / "output"
CODEQL_RESULTS = PIPELINE_DIR / "stage1_static" / "output" / "codeql_results"  # e.g. .../codeql_results/postgres/*.csv
SCHEDULER_LOG  = PIPELINE_DIR / "stage3_scheduler" / "output" / "scheduler.log"
BUILD_LOG      = PIPELINE_DIR / "output" / "build.log"
GRIFFIN_ROOT   = Path(os.environ.get("SQLEEK_GRIFFIN_ROOT", "/root/dfuzz-griffin"))
RUNTIME_CONFIG = PIPELINE_DIR / "stage3_scheduler" / "output" / "griffin_runtime.json"

DBMS_LIST  = ["sqlite", "postgres", "mysql", "mariadb", "monetdb"]
BUG_TYPES  = ["memory", "logic"]

# Seed scheduler thresholds
# Higher thresholds make the scheduler more selective. For this pipeline we
# want to keep more candidate seeds in play, so the default is lower.
HIGH_THRESHOLD = 0.4   # final score: replicate seed
LOW_THRESHOLD  = 0.03  # proximity score: defer seed
POLL_INTERVAL  = 60    # seconds between scheduler cycles
REPLICATE_N    = 5     # copies of a high-value seed to inject
GRIFFIN_IMAGES = {
    "sqlite": "griffin_sqlite",
    "postgres": "griffin_postgres",
    "mysql": "griffin_mysql",
}

GRIFFIN_CONTAINER_PREFIX = "sqleek_griffin"

MUTATOR_CANDIDATES = [
    "/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_{dbms}.so",
    "/workspace/bld_griffin_dynamic/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so",
    "/workspace/bld_griffin/custom_mutator/squirrel_dependencies/libsquirrel_{dbms}.so",
    "/workspace/bld_griffin/custom_mutator/squirrel_dependencies/libsquirrel_mysql.so",
    "/workspace/bld_griffin_dynamic/custom_mutator/libmerge_odbc_ver_dynamic.so",
]

HARNESS_CANDIDATES = [
    "/workspace/bld_griffin_dynamic/autodriver_odbc_v5_aflpp",
    "/workspace/bld_griffin/autodriver_odbc_v5_aflpp",
    "/workspace/scripts/testt",
]

# Fallback targets used when CodeQL finds nothing for a DBMS.
FALLBACK_TARGETS = {
    "sqlite_memory": [
        "sqlite3.c:74042", "sqlite3.c:82371", "sqlite3.c:87209",
        "sqlite3.c:91544", "sqlite3.c:108234",
    ],
    "postgres_memory": [
        "bufmgr.c:845", "execAgg.c:1205", "nodeSort.c:220", "tuplesort.c:1888",
    ],
    "mysql_memory": [
        "item.cc:6271", "sql_select.cc:2830", "opt_range.cc:11442",
    ],
    "mariadb_memory": [
        "item.cc:6271", "sql_select.cc:2830", "opt_range.cc:11442",
    ],
    "monetdb_memory": [
        "sql_scenario.c:1664", "rel_unnest.c:1", "mal_interpreter.c:490",
        "gdk_join.c:4536", "bs.c:50", "monetdbe.c:327",
    ],
}
