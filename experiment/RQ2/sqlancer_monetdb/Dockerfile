# syntax=docker/dockerfile:1.6

ARG SQLANCER_REPOSITORY=https://github.com/MonetDB/sqlancer.git
ARG SQLANCER_COMMIT=2be3b061845bb534a3dafba43628c6d00149410b

FROM maven:3.9-eclipse-temurin-11 AS builder
ARG SQLANCER_REPOSITORY
ARG SQLANCER_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
COPY oracle/MonetNoRECOracle.java /tmp/MonetNoRECOracle.java
RUN git clone --filter=blob:none "${SQLANCER_REPOSITORY}" sqlancer \
    && cd sqlancer \
    && git checkout "${SQLANCER_COMMIT}" \
    && cp /tmp/MonetNoRECOracle.java src/sqlancer/monet/oracle/MonetNoRECOracle.java \
    && mvn -B -DskipTests package

FROM eclipse-temurin:11-jre-jammy
COPY --from=builder /opt/sqlancer/target/sqlancer-2.0.0.jar /opt/sqlancer/sqlancer.jar
COPY --from=builder /opt/sqlancer/target/lib /opt/sqlancer/lib
WORKDIR /workspace
ENTRYPOINT ["java", "-jar", "/opt/sqlancer/sqlancer.jar"]
CMD ["monetdb", "--oracle=NOREC", "--num-threads=1", "--num-tries=1", "--max-generated-databases=1"]
