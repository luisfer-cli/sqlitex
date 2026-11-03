# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Builder: resolve dependencies and install SQLiteX into an isolated venv.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first to maximize layer caching.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

# Copy only the package source and install the project itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Runtime: minimal image, non-root user, /data as the working volume.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="SQLiteX" \
      org.opencontainers.image.description="Explore SQLite databases and query them in natural language with RAG + generated SQL." \
      org.opencontainers.image.source="https://github.com/luisfer-cli/sqlitex" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --user-group sqlitex \
    && mkdir -p /data \
    && chown -R sqlitex:sqlitex /data

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/sqlitex \
    SQLITEX_HOME=/data/.sqlitex

WORKDIR /data
USER sqlitex

ENTRYPOINT ["sqlitex"]
CMD ["--help"]
