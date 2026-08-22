# SQLaser MariaDB adapter

This image reuses SQLRight's MySQL 8.0.27 parser/fuzzer and runs it against an
instrumented MariaDB server. The SQLaser distance/energy patch is applied to
the SQLRight MySQL AFL source, and `mariadb_target_chains.tsv` is selected via
`SQLASER_TARGETS`.

Build from the SQLeek repository root so the Dockerfile can copy the pinned
SQLRight compatibility patch:

```bash
docker build -f experiment/RQ2/sqlaser/build_context/sqlright_mariadb/Dockerfile \
  -t sqlaser_mariadb:latest .
```

The container expects a mounted input corpus and output directory. A later
server run can use, for example:

```bash
docker run --rm \
  -v /path/to/inputs:/workspace/inputs:ro \
  -v /path/to/output:/workspace/output \
  -e DURATION=24h \
  sqlaser_mariadb:latest
```

No campaign is run as part of building or checking this adapter in the local
workspace.
