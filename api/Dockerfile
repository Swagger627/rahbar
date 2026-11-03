# RAHBAR API image. It serves /api/ask, /healthz, /readyz and /metrics, and only ever
# READS the index: indexing is the indexer Job's work (section 4.2), never this process's.
#
#   docker build -t rahbar-api:dev api
#
# Runs as a non-root user and needs no writable path except /tmp, so the pod can set
# readOnlyRootFilesystem: true with an emptyDir mounted at /tmp. Configuration comes from
# environment variables (the ConfigMap and Secret); nothing secret is baked in.

# Base pinned by tag and digest: Python 3.12 on Debian 13 (trixie), slim variant.
# ---- build stage: install the pinned dependencies into a self-contained virtualenv
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && /opt/venv/bin/python -m compileall -q /opt/venv

# ---- runtime stage: the virtualenv and the application code, nothing else
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime
# The root filesystem is read-only on the cluster, so Python must not try to write
# .pyc files at runtime (they are compiled at build time instead), and every temporary
# or home-directory write goes to /tmp.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TMPDIR=/tmp
RUN groupadd --system --gid 10001 rahbar \
 && useradd --system --uid 10001 --gid rahbar --no-create-home --shell /usr/sbin/nologin rahbar
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
RUN python -m compileall -q /app/app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
