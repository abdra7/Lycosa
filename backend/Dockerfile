FROM python:3.14-slim

# curl is used by the compose healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/lycosa

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY config ./config
COPY scripts ./scripts

ARG LYCOSA_EXTRAS=""
RUN if [ -n "$LYCOSA_EXTRAS" ]; then pip install --no-cache-dir ".[${LYCOSA_EXTRAS}]"; else pip install --no-cache-dir .; fi

EXPOSE 8000

# shared metric files so /metrics covers every uvicorn worker; emptied at each
# launch, before the workers start (stale files would add old values)
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/lycosa-prometheus

# migrations + admin bootstrap run before serving (see infra/docker-compose.yml).
# WORKERS>1 requires REDIS_URL (ADR-028) — the app fails fast otherwise.
CMD ["sh", "-c", "rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1}"]
