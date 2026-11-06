# Multi-stage build for FablaBot

# uv, pinned by version and digest to the release CI uses; copied only into the stages that run it.
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Base stage with shared configuration. The index digest pins the base for every platform; bump it with the tag.
FROM python:3.13-alpine@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8 AS base

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
