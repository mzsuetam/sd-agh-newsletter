# syntax=docker/dockerfile:1
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Install uv (fast, reproducible installs from uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first so they are cached independently of source edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Application code.
COPY README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev

# Runtime data (SQLite DB + previews) lives on a mounted volume.
RUN mkdir -p /data && chown -R 10001:10001 /data /app
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser

USER 10001
ENV DB_PATH=/data/app.db

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

# Default to the web server; the scheduler service overrides `command`.
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
