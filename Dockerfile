# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UVICORN_WORKERS=2 \
    HOST=0.0.0.0 \
    PORT=8000

# Install system deps and stockfish engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first for caching
COPY pyproject.toml /app/
COPY requirements.txt /app/

# Create venv and install Python deps
RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install -r /app/requirements.txt

# Copy application code and Alembic after deps for better layer caching
COPY alembic.ini /app/
COPY alembic /app/alembic
COPY cbuddy /app/cbuddy

# Install our package in editable mode
RUN . /opt/venv/bin/activate \
    && pip install -e /app

ENV PATH="/opt/venv/bin:$PATH"

# Runtime envs (override in docker-compose)
ENV DATABASE_URL="postgresql+psycopg://postgres:postgres@db:5432/chessbuddy" \
    CORS_ALLOW_ORIGINS="*" \
    ENGINE_PATH="/usr/bin/stockfish"

EXPOSE 8000

CMD alembic upgrade head && exec uvicorn cbuddy.api:app --host $HOST --port $PORT --workers $UVICORN_WORKERS
