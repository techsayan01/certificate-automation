# syntax=docker/dockerfile:1.6
# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt ./
RUN pip install --user -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user for Cloud Run best practice
RUN groupadd -r app && useradd -r -g app app

WORKDIR /app
ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Copy installed deps from builder
COPY --from=builder --chown=app:app /root/.local /home/app/.local

# Source
COPY --chown=app:app backend/ ./backend/

USER app

# Cloud Run sends PORT=8080 by default
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
