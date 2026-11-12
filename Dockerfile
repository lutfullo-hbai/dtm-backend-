# API image: one uvicorn worker per container (scale by running more containers).
# The same image runs migrations as a one-off process: `alembic upgrade head`.
# Base images are pinned to exact versions; bump them deliberately.

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

# --- builder: resolve the locked runtime dependencies into /opt/venv ----------------
# Same base as the runtime stage, so the venv's interpreter path is identical.
FROM python:3.13.15-slim-trixie AS builder

COPY --from=uv /uv /usr/local/bin/uv

# Compile bytecode at install time (the runtime never writes .pyc), copy files out of
# the cache instead of hard-linking, use the image's interpreter, never download one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
# No BuildKit-only features (cache mounts), so the classic builder works too; the
# layer is reused as long as the lock is unchanged. --no-cache keeps uv's cache out.
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# --- runtime: interpreter, venv and application code only (no uv, no compilers) ---
FROM python:3.13.15-slim-trixie AS runtime

ARG VCS_REF=unknown
ARG VCS_URL=https://github.com/lutfullo-hbai/dtm-backend-
LABEL org.opencontainers.image.title="dtm-backend" \
      org.opencontainers.image.description="Backend API for the DTM AI tutor platform." \
      org.opencontainers.image.source="${VCS_URL}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Fixed, unprivileged identity; no home directory and no login shell.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# Code stays root-owned and read-only to the app user; precompile it once here.
RUN python -m compileall -q app migrations

USER 10001:10001
EXPOSE 8000

# Liveness only: /health never touches PostgreSQL or Redis.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Exec form: uvicorn is PID 1 and receives SIGTERM (the default STOPSIGNAL) directly.
# In-flight requests get 8 s to finish, inside compose's default 10 s stop grace.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--timeout-graceful-shutdown", "8"]
