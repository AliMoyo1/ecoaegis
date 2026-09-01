# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS base

ARG VCS_REF=unknown

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ONLY_BINARY=:all: \
    TZ=UTC

LABEL org.opencontainers.image.title="EcoAegis" \
      org.opencontainers.image.description="EcoAegis SHE management platform" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/AliMoyo1/ecoaegis"

WORKDIR /app

RUN groupadd --gid 10001 ecoaegis \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin ecoaegis

COPY deploy/ecoaegis/requirements-prod.lock /tmp/requirements-prod.lock
RUN python -m pip install --requirement /tmp/requirements-prod.lock \
    && rm -f /tmp/requirements-prod.lock

COPY --chown=10001:10001 sheplatform /app/sheplatform
COPY deploy/ecoaegis/entrypoint.sh /usr/local/bin/ecoaegis-entrypoint
COPY deploy/ecoaegis/healthcheck.py /usr/local/bin/ecoaegis-healthcheck.py

RUN chmod 0555 /usr/local/bin/ecoaegis-entrypoint \
    && chmod 0444 /usr/local/bin/ecoaegis-healthcheck.py \
    && install -d -m 0750 -o ecoaegis -g ecoaegis \
       /app/sheplatform/data \
       /app/sheplatform/data/attachments \
       /app/sheplatform/modules/data \
       /app/sheplatform/modules/data/evidence

USER 10001:10001
EXPOSE 8080

FROM base AS test

USER root
COPY deploy/ecoaegis/requirements-test.lock /tmp/requirements-test.lock
RUN python -m pip install --requirement /tmp/requirements-test.lock \
    && rm -f /tmp/requirements-test.lock
COPY --chown=10001:10001 tests /app/tests
COPY --chown=10001:10001 scripts /app/scripts
COPY --chown=10001:10001 .env.example /app/.env.example
USER 10001:10001

ENTRYPOINT ["/usr/local/bin/ecoaegis-entrypoint"]
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]

FROM base AS production

ENTRYPOINT ["/usr/local/bin/ecoaegis-entrypoint"]
CMD ["uvicorn", "sheplatform.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
