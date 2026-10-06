# Runner image for fixture replays and the end-to-end proof.
#
# Expanso Edge and CLI use their latest releases. Python and OpenSSL are here
# for the S3 fixture service and the correlator subprocess.
FROM ghcr.io/expanso-io/expanso-edge:latest

USER root

RUN apk add --no-cache bash ca-certificates curl openssl python3 \
 && curl -fsSL https://get.expanso.io/cli/install.sh \
    | EXPANSO_INSTALL_DIR=/usr/local/bin bash \
 && expanso-cli version

COPY replay-entrypoint.sh /usr/local/bin/replay-entrypoint
RUN chmod 0755 /usr/local/bin/replay-entrypoint

ENTRYPOINT ["/usr/local/bin/replay-entrypoint"]
