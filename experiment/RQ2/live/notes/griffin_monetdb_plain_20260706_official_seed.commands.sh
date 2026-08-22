# Updated at 2026-07-06T14:19:34Z
# Added only official MonetDB seeds:
docker cp /root/dfuzz-griffin/docker/metadata_collector/input-set/officials_to_griffin_compatible/official_monetdb/. griffin_monetdb:/workspace/seeds/
docker exec griffin_monetdb bash -lc "/workspace/scripts/start_all.sh"
