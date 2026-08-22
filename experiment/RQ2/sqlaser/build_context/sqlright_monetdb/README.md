# SQLaser MonetDB adapter

This image reuses SQLRight's SQLite parser/IR mutator and executes generated
inputs through a fresh, AFL-instrumented MonetDB `mserver5` instance. The
SQLaser SQLite distance/energy patch is combined with the existing SQLeek
coverage-only MonetDB compatibility patches, and
`monetdb_target_chains.tsv` is selected with `SQLASER_TARGETS`.

Build from the SQLeek repository root:

```bash
docker build -f experiment/RQ2/sqlaser/build_context/sqlright_monetdb/Dockerfile \
  -t sqlaser_monetdb:latest .
```

The image expects a mounted SQL seed corpus and output directory. A later
server run can use, for example:

```bash
docker run --rm \
  -v /path/to/inputs:/workspace/inputs:ro \
  -v /path/to/output:/workspace/output \
  -e DURATION=24h \
  sqlaser_monetdb:latest
```

The local task only checks patch order, paths, scripts, and target-file
format; it does not start MonetDB or run a fuzzing campaign.
