FROM python:3.13-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files
COPY pyproject.toml ./
RUN uv sync --frozen --no-install-project || uv sync --no-install-project

# Copy source code
COPY codehub ./codehub

# Copy Alembic files
COPY alembic.ini ./
COPY alembic ./alembic

# Install project
RUN uv sync --frozen || uv sync

# Expose port
EXPOSE 8000

# Default workers (can be overridden via environment variable)
ENV WORKERS=1

# Run application with configurable workers
CMD uv run uvicorn codehub.app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --no-access-log
