# Build context: repository root (needs backend/ and config/).
FROM python:3.12.15-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    CONFIG_DIR=/config

WORKDIR /app

# Dependencies first for layer caching.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY config /config
COPY backend /app

# The hfcache volume mounts at ~/.cache; creating it here makes the volume owned by `app`.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache \
    && chown -R app /app /home/app/.cache
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
