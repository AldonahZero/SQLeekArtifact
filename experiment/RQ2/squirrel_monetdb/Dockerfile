# syntax=docker/dockerfile:1.6

FROM ubuntu:22.04
LABEL maintainer="SQLeek"

ARG DEBIAN_FRONTEND=noninteractive
ARG SQUIRREL_REPOSITORY=https://github.com/s3team/Squirrel.git
ARG SQUIRREL_COMMIT=96810fd96cfb40fe8d899e8d7a4f3eaae191d1db
ARG AFLPLUSPLUS_COMMIT=8cdc48f73a17ddd557897f2098937a8ba3bfe184
ARG ABSEIL_COMMIT=e51b4ef779f58b89563bbf8778f05766ba110e10
ARG MONETDB_REPOSITORY=https://github.com/MonetDB/MonetDB.git
ARG MONETDB_COMMIT=9b26fc8e25a3948fc94486caae3e30103330a5a7

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison build-essential ca-certificates clang cmake curl \
    flex g++ gcc git libatomic1 libbz2-dev libcurl4-openssl-dev libgeos-dev \
    liblz4-dev libproj-dev \
    liblzma-dev libpcre2-dev libreadline-dev libssl-dev libtool libxml2-dev \
    llvm llvm-dev lld make ninja-build pkg-config python3 python3-dev \
    python3-fire libyaml-cpp-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
COPY squirrel-monetdb.patch /tmp/squirrel-monetdb.patch

RUN mkdir -p squirrel \
    && curl -fL --retry 5 --retry-delay 2 \
      -o /tmp/squirrel.tar.gz \
      "https://codeload.github.com/s3team/Squirrel/tar.gz/${SQUIRREL_COMMIT}" \
    && tar -xzf /tmp/squirrel.tar.gz --strip-components=1 -C squirrel \
    && rm -f /tmp/squirrel.tar.gz \
    && cd squirrel \
    && rm -rf AFLplusplus third_party/abseil-cpp \
    && mkdir -p AFLplusplus third_party/abseil-cpp \
    && curl -fL --retry 5 --retry-delay 2 \
      -o /tmp/aflplusplus.tar.gz \
      "https://codeload.github.com/AFLplusplus/AFLplusplus/tar.gz/${AFLPLUSPLUS_COMMIT}" \
    && tar -xzf /tmp/aflplusplus.tar.gz --strip-components=1 -C AFLplusplus \
    && rm -f /tmp/aflplusplus.tar.gz \
    && curl -fL --retry 5 --retry-delay 2 \
      -o /tmp/abseil-cpp.tar.gz \
      "https://codeload.github.com/abseil/abseil-cpp/tar.gz/${ABSEIL_COMMIT}" \
    && tar -xzf /tmp/abseil-cpp.tar.gz --strip-components=1 -C third_party/abseil-cpp \
    && rm -f /tmp/abseil-cpp.tar.gz \
    && git apply /tmp/squirrel-monetdb.patch

RUN cd /opt/squirrel/AFLplusplus \
    && LLVM_CONFIG=llvm-config make -j"$(nproc)" \
    && test -x afl-fuzz \
    && test -x afl-clang-fast \
    && test -x afl-clang-fast++

RUN cmake -S /opt/squirrel -B /opt/squirrel/build \
    -DCMAKE_BUILD_TYPE=Release -DALL=OFF -DMONETDB=ON \
    && cmake --build /opt/squirrel/build -j"$(nproc)"

RUN mkdir -p monetdb-src \
    && curl -fL --retry 5 --retry-delay 2 \
      -o /tmp/monetdb.tar.gz \
      "https://codeload.github.com/MonetDB/MonetDB/tar.gz/${MONETDB_COMMIT}" \
    && tar -xzf /tmp/monetdb.tar.gz --strip-components=1 -C monetdb-src \
    && rm -f /tmp/monetdb.tar.gz

RUN CC=/opt/squirrel/AFLplusplus/afl-clang-fast \
    CXX=/opt/squirrel/AFLplusplus/afl-clang-fast++ \
    cmake -G Ninja /opt/monetdb-src -B /opt/monetdb-build \
      -DCMAKE_INSTALL_PREFIX=/opt/monetdb \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DASSERT=OFF -DSTRICT=OFF -DGEOM=ON -DWITH_TESTING=OFF \
      -DBUILD_TESTING=OFF -DWITH_READLINE=OFF \
    && ninja -C /opt/monetdb-build install \
    && rm -rf /opt/monetdb-src /opt/monetdb-build

RUN useradd -m -s /bin/bash squirrel \
    && printf 'user=monetdb\npassword=monetdb\n' > /home/squirrel/.monetdb \
    && chown squirrel:squirrel /home/squirrel/.monetdb

ENV HOME=/home/squirrel \
    DOTMONETDBFILE=/home/squirrel/.monetdb \
    PATH=/opt/monetdb/bin:/opt/squirrel/AFLplusplus:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/monetdb/lib:/opt/monetdb/lib/monetdb5 \
    AFL_MAP_SIZE=262144 \
    AFL_IGNORE_PROBLEMS=1 \
    AFL_IGNORE_PROBLEMS_COVERAGE=1

USER squirrel
WORKDIR /opt/squirrel/scripts/utils
ENTRYPOINT ["python3", "run.py", "monetdb", "../../data/fuzz_root/pqsql_input/"]
