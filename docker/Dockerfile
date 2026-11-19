# Multi-stage build for FablaBot

# uv, pinned by version and digest to the release CI uses; copied only into the stages that run it.
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# Base stage with shared configuration. The index digest pins the base for every platform; bump it with the tag.
FROM python:3.13-alpine@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e AS base

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1

# Development stage
FROM base AS development
COPY --from=uv /uv /usr/local/bin/uv
# The lock file pins the dependencies, dev group included.
COPY pyproject.toml uv.lock ./
# The source is mounted by compose.dev.yml, so only the dependencies are installed.
RUN uv sync --locked --no-install-project

# Build stage: the locked runtime environment, with its bytecode compiled ahead of time.
FROM base AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
# The build context is only the files the allow-list in .dockerignore lets through.
COPY . .
# Locked runtime dependencies are installed at build time, so the container starts offline.
RUN uv sync --locked --no-dev --no-editable --no-cache

# Production stage: the installed environment only, run by an unprivileged user.
FROM base AS production
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1
# The commit the image is built from, which /info shows. The release build passes it; without it /info shows none.
ARG REVISION=""
ENV FABLABOT_REVISION=$REVISION
COPY --from=build /app/.venv /app/.venv
# A numeric user needs no passwd entry. data/ and logs/ come from volumes owned by it; the image
# creates neither, so a missing volume stops the bot at start instead of writing into the container.
USER 10001:10001

# Python as PID 1 ignores SIGTERM, so docker stop sends SIGINT and the bot exits at once.
STOPSIGNAL SIGINT

# The bot touches a heartbeat file every 30 seconds while connected; no port is needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=1 CMD ["python", "-m", "fablabot.healthcheck"]

# Run from the built environment, nothing is resolved at start.
CMD ["python", "-m", "fablabot.main"]
