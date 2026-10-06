# Runner image for fixture replays and the end-to-end proof.
#
# Expanso Edge is pinned by digest (v2.1.21, multi-arch index, the version the public-bar workflow pins) and the CLI by
# version, so a replay today and in a year use the same engine. Python and
# OpenSSL are here for the S3 fixture service and the correlator subprocess.
FROM ghcr.io/expanso-io/expanso-edge@sha256:2a9ec67c90075b0bf33811232971cd4ee03326181ed4186c799986cf9d371b38

USER root

RUN apk add --no-cache bash ca-certificates curl openssl python3 \
 && curl -fsSL https://get.expanso.io/cli/install.sh \
    | EXPANSO_INSTALL_DIR=/usr/local/bin EXPANSO_VERSION=v2.1.21 bash \
 && expanso-cli version

COPY replay-entrypoint.sh /usr/local/bin/replay-entrypoint
RUN chmod 0755 /usr/local/bin/replay-entrypoint

ENTRYPOINT ["/usr/local/bin/replay-entrypoint"]
