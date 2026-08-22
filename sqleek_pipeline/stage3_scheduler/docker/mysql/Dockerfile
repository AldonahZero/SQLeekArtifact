# syntax=docker/dockerfile:1.6

ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS deps
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl flex \
    default-libmysqlclient-dev g++ gcc gcc-11-plugin-dev git libaio-dev libcurl4-openssl-dev libedit-dev libevent-dev libffi-dev \
    liblz4-dev libncurses-dev libnuma-dev libreadline-dev libssl-dev libtool libzstd-dev \
    llvm llvm-dev llvm-14-dev lld make ninja-build openssh-client openssl patch pkg-config python3 python3-dev python3-pip \
    screen sudo unzip wget xz-utils zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir psutil mysql-connector-python mysqlclient

FROM deps AS openssl111-builder
ARG OPENSSL111_URL=git@github.com:openssl/openssl.git
ARG OPENSSL111_TAG=OpenSSL_1_1_1w
ARG OPENSSL111_COMMIT=e04bd3433fd84e1861bf258ea37928d9845e6a86
WORKDIR /tmp/openssl-src
RUN --mount=type=ssh \
    git init \
  && git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && git remote add origin "${OPENSSL111_URL}" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git fetch --depth 1 origin "${OPENSSL111_TAG}" \
  && git checkout "${OPENSSL111_COMMIT}" \
  && test "$(git rev-parse HEAD)" = "${OPENSSL111_COMMIT}" \
  && ./config --prefix=/opt/openssl111 --openssldir=/opt/openssl111/ssl no-shared no-tests \
  && make -j"$(nproc)" \
  && make install_sw \
  && /opt/openssl111/bin/openssl version

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
  && test -x afl-clang-fast++ \
  && test -x afl-gcc-fast \
  && test -x afl-g++-fast

FROM deps AS sqlright-builder
ARG SQLRIGHT_URL=git@github.com:PSU-Security-Universe/sqlright.git
ARG SQLRIGHT_BRANCH=main
ARG SQLRIGHT_COMMIT=9457f0311b70562a3423ee86ac7e2ebdaaa6664b
WORKDIR /opt
COPY --from=openssl111-builder /opt/openssl111 /opt/openssl111
RUN --mount=type=ssh \
    git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --branch "${SQLRIGHT_BRANCH}" "${SQLRIGHT_URL}" sqlright \
  && cd sqlright \
  && git checkout "${SQLRIGHT_COMMIT}"
COPY docker/patches/sqlright/ /tmp/sqlright-patches/
RUN cd /opt/sqlright \
  && for p in /tmp/sqlright-patches/*.patch; do patch -p1 < "$p"; done
WORKDIR /opt/sqlright/MySQL/docker/src/parser
RUN --mount=type=ssh \
    GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone --depth 1 --branch mysql-8.0.27 git@github.com:mysql/mysql-server.git mysql-server
ARG BOOST_173_URL=https://downloads.sourceforge.net/project/boost/boost/1.73.0/boost_1_73_0.tar.bz2?use_mirror=twds
ARG BOOST_173_SHA256=4eb3b8d442b426dc35346235c8733b5ae35ba431690e38c6a8263dce9fcbb402
RUN mkdir -p /opt/sqlright/MySQL/docker/src/parser/mysql-server/boost \
  && curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 "${BOOST_173_URL}" -o /tmp/boost_1_73_0.tar.bz2 \
  && echo "${BOOST_173_SHA256}  /tmp/boost_1_73_0.tar.bz2" | sha256sum -c - \
  && tar -xjf /tmp/boost_1_73_0.tar.bz2 -C /opt/sqlright/MySQL/docker/src/parser/mysql-server/boost \
  && test -f /opt/sqlright/MySQL/docker/src/parser/mysql-server/boost/boost_1_73_0/boost/version.hpp \
  && ln -s boost/boost_1_73_0 /opt/sqlright/MySQL/docker/src/parser/mysql-server/boost_1_73_0 \
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
ARG MYSQL_SSH_URL=git@github.com:mysql/mysql-server.git
ARG MYSQL_COMMIT=447eb26e094b444a88c532028647e48228c3c04f
WORKDIR /tmp/mysql-src
RUN --mount=type=ssh \
    git init \
  && git config --global url."git@github.com:".insteadOf "https://github.com/" \
  && git remote add origin "${MYSQL_SSH_URL}" \
  && GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git fetch --depth 1 origin "${MYSQL_COMMIT}" \
  && git checkout FETCH_HEAD
WORKDIR /tmp/mysql-build
RUN CC=/opt/aflplusplus/afl-gcc-fast \
    CXX=/opt/aflplusplus/afl-g++-fast \
    cmake /tmp/mysql-src \
      -DCMAKE_INSTALL_PREFIX=/opt/dbms \
      -DWITH_ROUTER=OFF \
      -DWITH_NDB=OFF \
      -DWITH_NDBCLUSTER_STORAGE_ENGINE=0 \
      -DWITH_UNIT_TESTS=OFF \
      -DWITHOUT_GROUP_REPLICATION=1 \
      -DWITH_SSL=system \
  && cmake --build . --target install --parallel "$(nproc)" \
  && rm -rf /opt/dbms/mysql-test /tmp/mysql-src /tmp/mysql-build

FROM deps AS runtime
ARG DBMS=mysql
ENV DBMS=${DBMS} \
    PATH=/opt/aflplusplus:/opt/dbms/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SEED_DIR=/workspace/seeds \
    TARGET_DIR=/workspace/targets \
    OUTPUT_DIR=/workspace/output \
    LOG_DIR=/workspace/logs \
    RUNTIME_DIR=/workspace/runtime \
    DURATION=24h
RUN useradd -ms /bin/bash mysql || true
COPY --from=afl-builder /opt/aflplusplus /opt/aflplusplus
COPY --from=sqlright-builder /opt/sqlright /opt/sqlright
COPY docker/patches/sqlright-runtime/ /tmp/sqlright-runtime-patches/
RUN cd /opt/sqlright \
  && for p in /tmp/sqlright-runtime-patches/*.patch; do patch -p1 < "$p"; done \
  && cd /opt/sqlright/MySQL/docker/src/AFL \
  && g++ -MT afl-fuzz.o -MMD -MP -MF .deps/afl-fuzz.d \
       -std=c++17 -fpermissive -static-libstdc++ -O3 -g -w \
       -DAFL_PATH=\"/usr/local/lib/afl\" \
       -DDOC_PATH=\"/usr/local/share/doc/afl\" \
       -DBIN_PATH=\"/usr/local/bin\" \
       -I../parser/mysql-server/bld/include -I../parser/mysql-server/bld/include \
       -I../parser -I../parser/mysql-server \
       -c -g afl-fuzz.cpp -lmysqlclient \
  && cd /opt/sqlright/MySQL/docker/src \
  && make -j"$(nproc)" afl-fuzz \
  && cp afl-fuzz /opt/sqlright/MySQL/docker/fuzz_root/afl-fuzz \
  && test -x /opt/sqlright/MySQL/docker/src/afl-fuzz \
  && test -x /opt/sqlright/MySQL/docker/fuzz_root/afl-fuzz \
  && rm -rf /tmp/sqlright-runtime-patches
COPY --from=dbms-builder /opt/dbms /opt/dbms
COPY . /opt/sqleek/stage3_scheduler
RUN chmod +x /opt/sqleek/stage3_scheduler/docker/common/*.sh \
  && mkdir -p /workspace/seeds /workspace/targets /workspace/output /workspace/logs /workspace/runtime \
  && chown -R mysql:mysql /workspace /opt/dbms \
  && mkdir -p /opt/dbms/data_all/ori_data \
  && chown -R mysql:mysql /opt/dbms/data_all \
  && su mysql -c '/opt/dbms/bin/mysqld --initialize-insecure --basedir=/opt/dbms --datadir=/opt/dbms/data_all/ori_data' \
  && su mysql -c '/opt/dbms/bin/mysqld --daemonize --basedir=/opt/dbms --datadir=/opt/dbms/data_all/ori_data --socket=/tmp/sqleek_mysql_init.sock --pid-file=/tmp/sqleek_mysql_init.pid --skip-networking --performance_schema=OFF' \
  && for i in $(seq 1 60); do /opt/dbms/bin/mysqladmin --protocol=socket --socket=/tmp/sqleek_mysql_init.sock -uroot ping >/dev/null 2>&1 && break; sleep 1; done \
  && /opt/dbms/bin/mysql --protocol=socket --socket=/tmp/sqleek_mysql_init.sock -uroot -e "CREATE DATABASE IF NOT EXISTS test_init; CREATE DATABASE IF NOT EXISTS test_sqlright1;" \
  && /opt/dbms/bin/mysqladmin --protocol=socket --socket=/tmp/sqleek_mysql_init.sock -uroot shutdown \
  && rm -f /tmp/sqleek_mysql_init.sock /tmp/sqleek_mysql_init.pid \
  && chown -R mysql:mysql /opt/dbms/data_all
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD /opt/sqleek/stage3_scheduler/docker/common/healthcheck.sh || exit 1
ENTRYPOINT ["/opt/sqleek/stage3_scheduler/docker/common/entrypoint.sh"]
CMD ["run"]
