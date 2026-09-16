FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin zero \
    && mkdir -p /opt/zero /var/lib/zero /run/zero \
    && chown -R zero:zero /opt/zero /var/lib/zero /run/zero

WORKDIR /opt/zero
COPY --chown=zero:zero . /opt/zero
USER zero
ENV PATH="/home/zero/.foundry/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    ZERO_WORKDIR=/opt/zero \
    ZERO_LEDGER_PATH=/var/lib/zero/zero_ledger.db \
    ZERO_RUNTIME_STATUS_PATH=/run/zero/status.json

RUN curl -L https://foundry.paradigm.xyz | bash \
    && /home/zero/.foundry/bin/foundryup

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -m zero.cli health --status "$ZERO_RUNTIME_STATUS_PATH" --max-age 180 || exit 1

VOLUME ["/var/lib/zero"]
CMD ["/opt/zero/scripts/run_service.sh"]
