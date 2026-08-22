# syntax=docker/dockerfile:1.6

ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl flex \
    g++ gcc gcc-11-plugin-dev git libatomic1 libbz2-dev libcurl4-openssl-dev \
    liblz4-dev liblzma-dev libpcre2-dev libreadline-dev libssl-dev libtool \
    libxml2-dev llvm llvm-dev llvm-14-dev lld make ninja-build openssh-client \
    patch perl pkg-config python3 python3-dev python3-pip sudo unzip wget zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir psutil

FROM deps AS afl-builder
ARG AFLPLUSPLUS_URL=git@github.com:AFLplusplus/AFLplusplus.git
ARG AFLPLUSPLUS_BRANCH=stable
ARG AFLPLUSPLUS_COMMIT=011cd189801830253c66ecd3cd6919ec01b46c34
WORKDIR /opt
RUN --mount=type=ssh \
    git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && for i in 1 2 3; do \
      GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --filter=blob:none --branch "${AFLPLUSPLUS_BRANCH}" "${AFLPLUSPLUS_URL}" aflplusplus && break; \
      rm -rf aflplusplus; sleep 10; \
    done \
  && cd aflplusplus \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git fetch --depth 1 origin "${AFLPLUSPLUS_COMMIT}" \
  && git checkout "${AFLPLUSPLUS_COMMIT}" \
  && make -j"$(nproc)" all \
  && test -x afl-fuzz \
  && test -x afl-showmap \
  && test -x afl-clang-fast \
  && test -x afl-clang-fast++

FROM deps AS dbms-builder
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
ARG MONETDB_SSH_URL=git@github.com:MonetDB/MonetDB.git
ARG MONETDB_COMMIT=9b26fc8e25a3948fc94486caae3e30103330a5a7
WORKDIR /tmp/monetdb-src
RUN --mount=type=ssh \
    git init \
  && git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && git remote add origin "${MONETDB_SSH_URL}" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git fetch --depth 1 origin "${MONETDB_COMMIT}" \
  && git checkout FETCH_HEAD \
  && test "$(git rev-parse HEAD)" = "${MONETDB_COMMIT}"
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
ARG SQLRIGHT_URL=git@github.com:PSU-Security-Universe/sqlright.git
ARG SQLRIGHT_BRANCH=main
ARG SQLRIGHT_COMMIT=9457f0311b70562a3423ee86ac7e2ebdaaa6664b
WORKDIR /opt
RUN --mount=type=ssh \
    git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --branch "${SQLRIGHT_BRANCH}" "${SQLRIGHT_URL}" sqlright \
  && cd sqlright \
  && git checkout "${SQLRIGHT_COMMIT}" \
  && test "$(git rev-parse HEAD)" = "${SQLRIGHT_COMMIT}"
COPY docker/patches/sqlright-sqlite-monetdb/ /tmp/sqlright-sqlite-monetdb-patches/
RUN cd /opt/sqlright \
  && for p in /tmp/sqlright-sqlite-monetdb-patches/*.patch; do patch -p1 < "$p"; done \
  && cd /opt/sqlright/SQLite/docker/src \
  && make -j"$(nproc)" \
  && cp afl-fuzz /opt/sqlright/SQLite/docker/fuzz_root/afl-fuzz \
  && test -x /opt/sqlright/SQLite/docker/src/afl-fuzz \
  && test -x /opt/sqlright/SQLite/docker/fuzz_root/afl-fuzz \
  && rm -rf /tmp/sqlright-sqlite-monetdb-patches

FROM deps AS runtime
ENV DBMS=monetdb \
    PATH=/opt/aflplusplus:/opt/dbms/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/dbms/lib:/opt/dbms/lib/monetdb5 \
    SEED_DIR=/workspace/seeds TARGET_DIR=/workspace/targets OUTPUT_DIR=/workspace/output \
    LOG_DIR=/workspace/logs RUNTIME_DIR=/workspace/runtime DURATION=24h \
    SQLEEK_MONETDB_AFL_TIMEOUT=20000 SQLEEK_MONETDB_SEED_TIMEOUT=8 SQLEEK_MONETDB_COVERAGE_ONLY=1 AFL_MAP_SIZE=262144 AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1
RUN useradd -ms /bin/bash monetdb || true
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
COPY --from=sqlright-builder /opt/sqlright /opt/sqlright
COPY --from=dbms-builder /opt/dbms /opt/dbms
COPY . /opt/sqleek/stage3_scheduler
RUN chmod -R a+rX /opt/sqleek \
  && chmod +x /opt/sqleek/stage3_scheduler/docker/common/*.sh \
  && mkdir -p /workspace/seeds /workspace/targets /workspace/output /workspace/logs /workspace/runtime \
  && mkdir -p /opt/dbms/data_all/ori_data \
  && chown -R monetdb:monetdb /workspace /opt/dbms \
  && printf 'SELECT 1;\n' > /tmp/sqleek_monetdb_init.sql \
  && su monetdb -c 'RUNTIME_DIR=/tmp/sqleek_monetdb_runtime LOG_DIR=/tmp SQLEEK_MONETDB_ROOT=/opt/dbms SQLEEK_MONETDB_SEED_TIMEOUT=3 bash /opt/sqleek/stage3_scheduler/docker/common/monetdb_single_wrapper.sh /tmp/sqleek_monetdb_init.sql' \
  && rm -rf /tmp/sqleek_monetdb_runtime /tmp/monetdb_server /tmp/sqleek_monetdb_init.sql
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD /opt/sqleek/stage3_scheduler/docker/common/healthcheck.sh || exit 1
ENTRYPOINT ["/opt/sqleek/stage3_scheduler/docker/common/entrypoint.sh"]
CMD ["run"]
