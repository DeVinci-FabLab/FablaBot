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

# Copy only pyproject.toml for installing dependencies
COPY pyproject.toml .
# Install in development mode with dev dependencies
RUN uv pip install --system .

# Production stage
FROM base AS production

# Install Selenium dependencies
RUN apk add --no-cache chromium chromium-chromedriver

# Set Chrome options for containerized environment
ENV CHROME_BIN=/usr/bin/chromium-browser \
    CHROME_PATH=/usr/lib/chromium/

# Copy all project files
COPY . .

# Install the application
RUN uv pip install --system .

# Run the main module
CMD ["uv", "run", "-m", "fablabot.main"]