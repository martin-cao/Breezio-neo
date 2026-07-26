ARG RUSTFS_IMAGE=rustfs/rustfs:latest
FROM ${RUSTFS_IMAGE}

USER root
RUN mkdir -p /data /logs && chown -R 10001:10001 /data /logs || true
USER 10001

