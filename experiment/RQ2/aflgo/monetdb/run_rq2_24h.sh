#!/usr/bin/env bash
set -e
repeat_id=${1:?usage: run_rq2_24h.sh <repeat-id>}
export RUN_ID="monetdb_aflgo_${repeat_id}"
export OUT_ROOT=/root/SQLeek/experiment/RQ2/aflgo/monetdb/output_rq2_24h/monetdb_aflgo_${repeat_id}_$(date +%Y%m%d_%H%M%S)
exec /root/SQLeek/experiment/RQ2/aflgo/monetdb/run_rq2_sanity.sh 86400
