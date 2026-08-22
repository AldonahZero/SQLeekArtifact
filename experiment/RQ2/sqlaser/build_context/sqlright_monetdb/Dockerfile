# syntax=docker/dockerfile:1.6

# Build from the SQLeek repository root:
# docker build -f experiment/RQ2/sqlaser/build_context/sqlright_monetdb/Dockerfile .

ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl flex \
    g++ gcc git libatomic1 libbz2-dev libcurl4-openssl-dev liblz4-dev \
    liblzma-dev libpcre2-dev libreadline-dev libssl-dev libtool libxml2-dev \
    llvm llvm-dev lld make ninja-build patch perl pkg-config python3 \
    openssh-client python3-dev python3-pip sudo unzip wget zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir psutil
RUN mkdir -p -m 0700 /root/.ssh \
  && ssh-keyscan -t ed25519 github.com > /root/.ssh/known_hosts

FROM deps AS afl-builder
ARG AFLPLUSPLUS_COMMIT=011cd189801830253c66ecd3cd6919ec01b46c34
WORKDIR /opt
RUN --mount=type=ssh git clone --filter=blob:none --branch stable git@github.com:AFLplusplus/AFLplusplus.git aflplusplus \
  && cd aflplusplus \
  && git fetch --depth 1 origin "$AFLPLUSPLUS_COMMIT" \
  && git checkout "$AFLPLUSPLUS_COMMIT" \
  && make -j"$(nproc)" all \
  && test -x afl-clang-fast \
  && test -x afl-clang-fast++ \
  && test -x afl-fuzz

FROM deps AS dbms-builder
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
ARG MONETDB_COMMIT=9b26fc8e25a3948fc94486caae3e30103330a5a7
WORKDIR /tmp/monetdb-src
RUN --mount=type=ssh git init \
  && git remote add origin git@github.com:MonetDB/MonetDB.git \
  && git fetch --depth 1 origin "$MONETDB_COMMIT" \
  && git checkout FETCH_HEAD \
  && test "$(git rev-parse HEAD)" = "$MONETDB_COMMIT"
WORKDIR /tmp/monetdb-build
RUN CC=/opt/aflplusplus/afl-clang-fast \
    CXX=/opt/aflplusplus/afl-clang-fast++ \
    cmake -G Ninja /tmp/monetdb-src \
      -DCMAKE_INSTALL_PREFIX=/opt/dbms \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DASSERT=OFF \
      -DSTRICT=OFF \
      -DWITH_TESTING=OFF \
      -DBUILD_TESTING=OFF \
      -DWITH_READLINE=OFF \
  && ninja install \
  && rm -rf /tmp/monetdb-src /tmp/monetdb-build

FROM deps AS sqlright-builder
ARG SQLRIGHT_COMMIT=9457f0311b70562a3423ee86ac7e2ebdaaa6664b
WORKDIR /opt
RUN --mount=type=ssh git clone --filter=blob:none --branch main git@github.com:PSU-Security-Universe/sqlright.git sqlright \
  && cd sqlright \
  && git fetch --depth 1 origin "$SQLRIGHT_COMMIT" \
  && git checkout "$SQLRIGHT_COMMIT" \
  && test "$(git rev-parse HEAD)" = "$SQLRIGHT_COMMIT"
COPY experiment/RQ2/sqlaser/patches/sqlaser_distance_energy_latest.patch /tmp/sqlaser-sqlite.patch
COPY sqleek_pipeline/stage3_scheduler/docker/patches/sqlright-sqlite-monetdb/0002-sqleek-monetdb-coverage-only.patch /tmp/monetdb-coverage-only.patch
COPY sqleek_pipeline/stage3_scheduler/docker/patches/sqlright-sqlite-monetdb/0003-sqleek-monetdb-sync-coverage-only.patch /tmp/monetdb-sync-coverage-only.patch
COPY sqleek_pipeline/stage3_scheduler/docker/patches/sqlright-sqlite-monetdb/0004-sqleek-monetdb-sync-directed-retention.patch /tmp/monetdb-sync-directed-retention.patch
RUN cd /opt/sqlright \
  && git apply /tmp/sqlaser-sqlite.patch \
  && git apply /tmp/monetdb-coverage-only.patch \
  && git apply /tmp/monetdb-sync-coverage-only.patch \
  && git apply /tmp/monetdb-sync-directed-retention.patch \
  && make -C SQLite/docker/src -j"$(nproc)" \
  && cp SQLite/docker/src/afl-fuzz SQLite/docker/fuzz_root/afl-fuzz \
  && test -x SQLite/docker/src/afl-fuzz \
  && test -x SQLite/docker/fuzz_root/afl-fuzz

FROM deps AS runtime
ENV DBMS=monetdb \
    PATH=/opt/aflplusplus:/opt/dbms/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SQLRIGHT_ROOT=/opt/sqlright \
    INPUT_DIR=/workspace/inputs \
    OUTPUT_DIR=/workspace/output \
    DURATION=24h \
    AFL_MAP_SIZE=262144
RUN useradd -ms /bin/bash monetdb
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
COPY --from=dbms-builder /opt/dbms /opt/dbms
COPY --from=sqlright-builder /opt/sqlright /opt/sqlright
COPY experiment/RQ2/sqlaser/build_context/sqlright_monetdb/monetdb_target_chains.tsv /opt/sqlright/SQLite/docker/fuzz_root/sqlaser/monetdb_target_chains.tsv
COPY experiment/RQ2/sqlaser/build_context/sqlright_monetdb/scripts/monetdb_single_wrapper.sh /usr/local/bin/monetdb_single_wrapper.sh
COPY experiment/RQ2/sqlaser/build_context/sqlright_monetdb/scripts/run_sqlaser_monetdb.sh /usr/local/bin/run_sqlaser_monetdb.sh
RUN set -eux; \
  test -x /opt/dbms/bin/mserver5; \
  test -x /opt/dbms/bin/mclient; \
  test -x /opt/sqlright/SQLite/docker/fuzz_root/afl-fuzz; \
  chmod +x /usr/local/bin/monetdb_single_wrapper.sh /usr/local/bin/run_sqlaser_monetdb.sh; \
  mkdir -p /workspace/inputs /workspace/output /workspace/runtime /workspace/logs /opt/dbms/data_all/ori_data; \
  chown -R monetdb:monetdb /workspace /opt/dbms; \
  test -f /opt/sqlright/SQLite/docker/fuzz_root/sqlaser/monetdb_target_chains.tsv
ENV LD_LIBRARY_PATH=/opt/dbms/lib:/opt/dbms/lib/monetdb5
ENTRYPOINT ["/usr/local/bin/run_sqlaser_monetdb.sh"]
