# Started at 2026-07-06T13:50:09Z
docker run --privileged -itd -m 70G --cpus=10 --shm-size=5G \
  -e SQLSIM_AFLPP_NEW_COV_SEED_ONLY=1 \
  -e NO_AFL_SHUFFLE_QUEUE=1 \
  -e SQLSIM_AFLPP_DISABLE_DRY_RUN=1 \
  -e SQLSIM_AFLPP_DISABLE_SYNC_BITMAP=1 \
  -e SQUIRREL_DISABLE_EXTRACT_STRUCT=1 \
  -e SQUIRREL_DISABLE_VALIDATE=1 \
  -e SQUIRREL_BOTH_MERGE_AND_UNMERGE=1 \
  --name griffin_monetdb_Griffin_rnd1_20260706 \
  griffin_monetdb:latest
# seeds: official_monetdb + select_only/monetdb + MorphMonetDB/final_seeds if image had none
# start:
docker exec griffin_monetdb_Griffin_rnd1_20260706 bash -lc "/workspace/scripts/start_all.sh"
