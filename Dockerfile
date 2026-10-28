# ==========================================
# Stage 1: Build Frontend (React + Vite)
# ==========================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package manifests for efficient layer caching
COPY frontend/package*.json ./

# Install npm dependencies
RUN npm ci

# Copy frontend source code
COPY frontend/ ./

# Build production bundle (outputs to /app/frontend/dist)
RUN npm run build

# ==========================================
# Stage 2: Runtime (FastAPI + Python 3.12)
# ==========================================
FROM python:3.12-slim AS runner

WORKDIR /app

# Install curl for container health check and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements for layer caching
COPY backend/requirements.txt ./backend/requirements.txt
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend source code and runtime definitions
COPY backend/ ./backend/
COPY whatsapp_flow_definition.json ./

# Copy built frontend assets from builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create directory for persistent SQLite database and uploads
RUN mkdir -p /app/data

# Create dedicated non-root application user for container security
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --gid 1001 appuser && \
    chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Production environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    DATABASE_URL=sqlite:////app/data/holduntil.db

# Expose container port (Coolify configurable)
EXPOSE 8000

# Docker & Coolify container healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Start Uvicorn ASGI server with dynamic $PORT support
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

