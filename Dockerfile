# =============================================================================
# Legend Trade — backend + built frontend in one image.
#
# The frontend is compiled in a separate stage and the static bundle copied
# across, so the runtime image carries no Node toolchain.
# =============================================================================

# --- stage 1: build the UI ---------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# --- stage 2: runtime --------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/backend

WORKDIR /app

# curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-server.txt ./requirements-server.txt
RUN pip install --no-cache-dir -r requirements-server.txt

# Only the modules the trading API actually imports.
COPY trading/ ./trading/
COPY database/ ./database/
COPY backend/ ./backend/

COPY --from=frontend /build/dist ./static

# Run as a non-root user; the SQLite database lives in this user's home.
RUN useradd --create-home --shell /bin/bash legend \
    && mkdir -p /home/legend/.legend-trade \
    && chown -R legend:legend /home/legend /app
USER legend

ENV LEGEND_DB_PATH=/home/legend/.legend-trade/legend.db \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
