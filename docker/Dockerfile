# Multi-stage build for FablaBot

# Base stage with shared configuration
FROM python:3.13-alpine AS base

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apk add --no-cache gcc musl-dev

# Install uv package manager
RUN pip install --no-cache-dir uv

# Development stage
FROM base AS development

# The lock file pins the dependencies, dev group included.
COPY pyproject.toml uv.lock ./
# The source is mounted by compose.dev.yml, so only the dependencies are installed.
RUN uv sync --locked --no-install-project

# Production stage
FROM base AS production

# The build context is only the files the allow-list in .dockerignore lets through.
COPY . .

# Locked runtime dependencies are installed at build time, so the container starts offline.
RUN uv sync --locked --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"

# Run from the built environment, nothing is resolved at start.
CMD ["python", "-m", "fablabot.main"]
