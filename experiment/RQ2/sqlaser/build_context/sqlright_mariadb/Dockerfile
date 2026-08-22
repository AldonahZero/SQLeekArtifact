# syntax=docker/dockerfile:1.6

# Build from the SQLeek repository root:
# docker build -f experiment/RQ2/sqlaser/build_context/sqlright_mariadb/Dockerfile .

ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl flex \
    default-libmysqlclient-dev g++ gcc git libaio-dev \
    libcurl4-openssl-dev libedit-dev libevent-dev libffi-dev liblz4-dev \
    libncurses-dev libnuma-dev libpam0g-dev libpcre2-dev libreadline-dev \
    libsnappy-dev libssl-dev libtool libxml2-dev libzstd-dev llvm llvm-dev \
    gcc-11-plugin-dev lld make ninja-build openssh-client openssl patch pkg-config python3 python3-dev python3-mysqldb \
    python3-pip screen sudo unzip wget xz-utils zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir psutil
RUN mkdir -p -m 0700 /root/.ssh \
  && ssh-keyscan -t ed25519 github.com > /root/.ssh/known_hosts

FROM deps AS openssl111-builder
ARG OPENSSL111_COMMIT=e04bd3433fd84e1861bf258ea37928d9845e6a86
WORKDIR /tmp/openssl-src
RUN --mount=type=ssh git clone --depth 1 --branch OpenSSL_1_1_1w git@github.com:openssl/openssl.git . \
  && test "$(git rev-parse HEAD)" = "$OPENSSL111_COMMIT" \
  && ./config --prefix=/opt/openssl111 --openssldir=/opt/openssl111/ssl no-shared no-tests \
  && make -j"$(nproc)" \
  && make install_sw \
  && /opt/openssl111/bin/openssl version

FROM deps AS afl-builder
ARG AFLPLUSPLUS_COMMIT=011cd189801830253c66ecd3cd6919ec01b46c34
WORKDIR /opt
RUN --mount=type=ssh git clone --filter=blob:none --branch stable \
    git@github.com:AFLplusplus/AFLplusplus.git aflplusplus \
  && cd aflplusplus \
  && git fetch --depth 1 origin "${AFLPLUSPLUS_COMMIT}" \
  && git checkout "${AFLPLUSPLUS_COMMIT}" \
  && make -j"$(nproc)" all \
  && test -x afl-fuzz \
  && test -x afl-clang-fast \
  && test -x afl-clang-fast++ \
  && test -x afl-gcc-fast \
  && test -x afl-g++-fast

FROM deps AS sqlright-builder
ARG SQLRIGHT_COMMIT=9457f0311b70562a3423ee86ac7e2ebdaaa6664b
WORKDIR /opt
COPY --from=openssl111-builder /opt/openssl111 /opt/openssl111
RUN --mount=type=ssh \
  for attempt in 1 2 3 4 5; do \
    rm -rf sqlright; \
    git clone --filter=blob:none --depth 1 --branch main git@github.com:PSU-Security-Universe/sqlright.git sqlright && break; \
    test \"$attempt\" -lt 5 && sleep 10; \
  done \
  && test -d sqlright/.git \
  && cd sqlright \
  && git fetch --depth 1 origin "$SQLRIGHT_COMMIT" \
  && git checkout "$SQLRIGHT_COMMIT" \
  && test "$(git rev-parse HEAD)" = "$SQLRIGHT_COMMIT"
COPY sqleek_pipeline/stage3_scheduler/docker/patches/sqlright/0001-sqleek-mysql-runtime-layout.patch /tmp/sqlright-runtime-layout.patch
RUN cd /opt/sqlright && git apply /tmp/sqlright-runtime-layout.patch
WORKDIR /opt/sqlright/MySQL/docker/src/parser
RUN --mount=type=ssh git clone --depth 1 --branch mysql-8.0.27 git@github.com:mysql/mysql-server.git mysql-server
ARG BOOST_173_URL=https://downloads.sourceforge.net/project/boost/boost/1.73.0/boost_1_73_0.tar.bz2?use_mirror=twds
ARG BOOST_173_SHA256=4eb3b8d442b426dc35346235c8733b5ae35ba431690e38c6a8263dce9fcbb402
RUN mkdir -p mysql-server/boost \
  && curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 "$BOOST_173_URL" -o /tmp/boost_1_73_0.tar.bz2 \
  && echo "$BOOST_173_SHA256  /tmp/boost_1_73_0.tar.bz2" | sha256sum -c - \
  && tar -xjf /tmp/boost_1_73_0.tar.bz2 -C mysql-server/boost \
  && ln -s boost/boost_1_73_0 mysql-server/boost_1_73_0 \
  && rm -f /tmp/boost_1_73_0.tar.bz2
WORKDIR /opt/sqlright/MySQL/docker/src/parser/patches
RUN bash ./replace-changed-code.sh
WORKDIR /opt/sqlright/MySQL/docker/src/parser/mysql-server/bld
RUN cmake .. -DWITH_BOOST=../boost -DWITH_SSL=/opt/openssl111 -DUSE_LD_GOLD=1 \
  && make -j"$(nproc)"
WORKDIR /opt/sqlright/MySQL/docker/src
RUN make -j"$(nproc)" \
  && test -x afl-fuzz \
  && test -d /opt/sqlright/MySQL/docker/fuzz_root/mysql_initlib

FROM deps AS dbms-builder
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
ARG MARIADB_COMMIT=a06be0288f63f520aec95982cc6f872d26c4f23c
WORKDIR /tmp/mariadb-src
RUN --mount=type=ssh git init \
  && git remote add origin git@github.com:MariaDB/server.git \
  && git fetch --depth 1 origin "$MARIADB_COMMIT" \
  && git checkout FETCH_HEAD \
  && test "$(git rev-parse HEAD)" = "$MARIADB_COMMIT" \
  && git config --global url."git@github.com:".insteadOf https://github.com/ \
  && sed -i -E 's#https?://github.com/#git@github.com:#g' .gitmodules \
  && git submodule sync --recursive \
  && git submodule update --init --depth 1 libmariadb \
  && rm -rf libmariadb/.git
WORKDIR /tmp/mariadb-build
RUN CC=/opt/aflplusplus/afl-gcc-fast \
    CXX=/opt/aflplusplus/afl-g++-fast \
    cmake /tmp/mariadb-src \
      -DCMAKE_INSTALL_PREFIX=/opt/dbms \
      -DWITH_SSL=system \
      -DUPDATE_SUBMODULES=OFF \
      -DWITH_UNIT_TESTS=OFF \
      -DWITH_EMBEDDED_SERVER=OFF \
      -DWITH_WSREP=OFF \
      -DPLUGIN_ROCKSDB=NO \
      -DPLUGIN_TOKUDB=NO \
      -DPLUGIN_CONNECT=NO \
      -DPLUGIN_SPIDER=NO \
      -DPLUGIN_S3=NO \
  && cmake --build . --target install --parallel "$(nproc)" \
  && rm -rf /opt/dbms/mysql-test /tmp/mariadb-src /tmp/mariadb-build

FROM deps AS runtime
ENV DBMS=mariadb \
    PATH=/opt/aflplusplus:/opt/dbms/bin:/opt/dbms/scripts:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SQLRIGHT_ROOT=/opt/sqlright \
    SQLEEK_MYSQL_ROOT=/opt/dbms \
    INPUT_DIR=/workspace/inputs \
    OUTPUT_DIR=/workspace/output \
    DURATION=24h
RUN useradd -ms /bin/bash mysql
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
COPY --from=sqlright-builder /opt/sqlright /opt/sqlright
COPY --from=dbms-builder /opt/dbms /opt/dbms
COPY experiment/RQ2/sqlaser/patches/sqlaser_mysql827_distance_energy.patch /tmp/sqlaser-mysql.patch
RUN cd /opt/sqlright \
  && git apply /tmp/sqlaser-mysql.patch \
  && rm -f /tmp/sqlaser-mysql.patch
COPY experiment/RQ2/sqlaser/build_context/sqlright_mariadb/mariadb_target_chains.tsv /opt/sqlright/MySQL/docker/fuzz_root/sqlaser/mariadb_target_chains.tsv
COPY experiment/RQ2/sqlaser/build_context/sqlright_mariadb/scripts/run_sqlaser_mariadb.sh /usr/local/bin/run_sqlaser_mariadb.sh
COPY experiment/RQ2/sqlaser/build_context/sqlright_mariadb/scripts/sqlaser_mariadb_entrypoint.sh /usr/local/bin/sqlaser-mariadb-entrypoint
RUN set -eux; \
  cd /opt/sqlright/MySQL/docker/src/AFL; \
  g++ -MT afl-fuzz.o -MMD -MP -MF .deps/afl-fuzz.d \
    -std=c++17 -fpermissive -static-libstdc++ -O3 -g -w \
    -DAFL_PATH=\"/usr/local/lib/afl\" \
    -DDOC_PATH=\"/usr/local/share/doc/afl\" \
    -DBIN_PATH=\"/usr/local/bin\" \
    -I../parser/mysql-server/bld/include -I../parser \
    -I../parser/mysql-server -c -g afl-fuzz.cpp; \
  cd /opt/sqlright/MySQL/docker/src; \
  make -j"$(nproc)" afl-fuzz; \
  cp afl-fuzz /opt/sqlright/MySQL/docker/fuzz_root/afl-fuzz; \
  test -x /opt/sqlright/MySQL/docker/src/afl-fuzz; \
  test -x /opt/sqlright/MySQL/docker/fuzz_root/afl-fuzz; \
  test -x /opt/dbms/bin/mariadbd; \
  if [ ! -e /opt/dbms/bin/mysqld ]; then ln -s mariadbd /opt/dbms/bin/mysqld; fi; \
  if [ ! -e /opt/dbms/bin/mysql ]; then ln -s mariadb /opt/dbms/bin/mysql; fi; \
  if [ ! -e /opt/dbms/bin/mysqladmin ]; then ln -s mariadb-admin /opt/dbms/bin/mysqladmin; fi; \
  chmod +x /usr/local/bin/run_sqlaser_mariadb.sh /usr/local/bin/sqlaser-mariadb-entrypoint; \
  mkdir -p /workspace/inputs /workspace/output /opt/dbms/data_all/ori_data; \
  chown -R mysql:mysql /workspace /opt/dbms /opt/sqlright; \
  test -f /opt/sqlright/MySQL/docker/fuzz_root/sqlaser/mariadb_target_chains.tsv
ENV LD_LIBRARY_PATH=/opt/sqlright/MySQL/docker/src/parser/mysql-server/bld/library_output_directory:/opt/dbms/lib
ENTRYPOINT ["/usr/local/bin/sqlaser-mariadb-entrypoint"]
CMD ["/usr/local/bin/run_sqlaser_mariadb.sh"]
