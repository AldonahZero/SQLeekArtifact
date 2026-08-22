# syntax=docker/dockerfile:1.6

ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl flex \
    g++ gcc gcc-11-plugin-dev git libicu-dev liblz4-dev libpq-dev libreadline-dev \
    libssl-dev libxml2-dev libxslt1-dev libzstd-dev llvm llvm-dev llvm-14-dev lld \
    make ninja-build openssh-client patch perl pkg-config python3 python3-dev python3-pip \
    sudo unzip util-linux wget xz-utils zlib1g-dev \
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
ARG POSTGRES_SSH_URL=git@github.com:postgres/postgres.git
ARG POSTGRES_COMMIT=62d6c7d3df6287f1bd83199c1a746e50d31571a0
WORKDIR /tmp/postgres-src
RUN --mount=type=ssh \
    git init \
  && git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && git remote add origin "${POSTGRES_SSH_URL}" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git fetch --depth 1 origin "${POSTGRES_COMMIT}" \
  && git checkout FETCH_HEAD \
  && test "$(git rev-parse HEAD)" = "${POSTGRES_COMMIT}"
RUN CC=/opt/aflplusplus/afl-clang-fast \
    CXX=/opt/aflplusplus/afl-clang-fast++ \
    ./configure \
      --prefix=/opt/dbms \
      --enable-debug \
      --enable-cassert \
      --without-readline \
      --without-icu \
  && make -j"$(nproc)" \
  && make install \
  && rm -rf /tmp/postgres-src

FROM deps AS sqlright-builder
ARG SQLRIGHT_URL=git@github.com:PSU-Security-Universe/sqlright.git
ARG SQLRIGHT_BRANCH=main
ARG SQLRIGHT_COMMIT=9457f0311b70562a3423ee86ac7e2ebdaaa6664b
COPY --from=dbms-builder /opt/dbms /opt/dbms
WORKDIR /opt
RUN --mount=type=ssh \
    git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --branch "${SQLRIGHT_BRANCH}" "${SQLRIGHT_URL}" sqlright \
  && cd sqlright \
  && git checkout "${SQLRIGHT_COMMIT}" \
  && test "$(git rev-parse HEAD)" = "${SQLRIGHT_COMMIT}"
COPY docker/patches/sqlright-postgresql/ /tmp/sqlright-postgresql-patches/
RUN cd /opt/sqlright \
  && for p in /tmp/sqlright-postgresql-patches/*.patch; do patch -p1 < "$p"; done
WORKDIR /opt/sqlright/PostgreSQL/docker/src
# Link the SQLRight PostgreSQL fuzzer against the system client libpq. The
# DBMS server under /opt/dbms remains AFL++-instrumented; linking this fuzzer
# against /opt/dbms/lib/libpq.so would require AFL runtime symbols.
RUN make -j"$(nproc)" PGSQL_INCLUDE_PATH=/usr/include/postgresql PGSQL_LIB_PATH=/usr/lib/x86_64-linux-gnu \
  && cp afl-fuzz /opt/sqlright/PostgreSQL/docker/fuzz_root/afl-fuzz \
  && test -x /opt/sqlright/PostgreSQL/docker/src/afl-fuzz \
  && test -x /opt/sqlright/PostgreSQL/docker/fuzz_root/afl-fuzz \
  && rm -rf /tmp/sqlright-postgresql-patches

FROM deps AS runtime
ENV DBMS=postgres \
    PATH=/opt/aflplusplus:/opt/dbms/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/dbms/lib \
    AFL_MAP_SIZE=262144 \
    SEED_DIR=/workspace/seeds \
    TARGET_DIR=/workspace/targets \
    OUTPUT_DIR=/workspace/output \
    LOG_DIR=/workspace/logs \
    RUNTIME_DIR=/workspace/runtime \
    DURATION=24h
RUN useradd -ms /bin/bash postgres || true
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
COPY --from=sqlright-builder /opt/sqlright /opt/sqlright
COPY --from=dbms-builder /opt/dbms /opt/dbms
COPY . /opt/sqleek/stage3_scheduler
RUN chmod +x /opt/sqleek/stage3_scheduler/docker/common/*.sh \
  && mkdir -p /workspace/seeds /workspace/targets /workspace/output /workspace/logs /workspace/runtime \
  && mkdir -p /opt/dbms/data_all/ori_data \
  && chown -R postgres:postgres /workspace /opt/dbms \
  && su postgres -c 'AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1 /opt/dbms/bin/initdb -D /opt/dbms/data_all/ori_data -A trust -U postgres --locale=C' \
  && su postgres -c 'AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1 /opt/dbms/bin/pg_ctl -D /opt/dbms/data_all/ori_data -o "-h 127.0.0.1 -p 55432 -k /tmp" -l /tmp/sqleek_postgres_init.log start' \
  && for i in $(seq 1 60); do AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1 /opt/dbms/bin/pg_isready -h 127.0.0.1 -p 55432 -d postgres -U postgres >/dev/null 2>&1 && break; sleep 1; done \
  && su postgres -c 'AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1 /opt/dbms/bin/createdb -h 127.0.0.1 -p 55432 -U postgres x' \
  && su postgres -c 'AFL_IGNORE_PROBLEMS=1 AFL_IGNORE_PROBLEMS_COVERAGE=1 /opt/dbms/bin/pg_ctl -D /opt/dbms/data_all/ori_data -m fast stop' \
  && rm -f /tmp/sqleek_postgres_init.log \
  && chown -R postgres:postgres /opt/dbms/data_all
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD /opt/sqleek/stage3_scheduler/docker/common/healthcheck.sh || exit 1
ENTRYPOINT ["/opt/sqleek/stage3_scheduler/docker/common/entrypoint.sh"]
CMD ["run"]
