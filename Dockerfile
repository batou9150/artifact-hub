# One image, one Cloud Run service: REST API + sandbox content endpoint (/a/*) +
# MCP endpoint (/mcp) + the built SPA. See docs/architecture.md, "Why one service".

# ── build the SPA ───────────────────────────────────────────────────────────
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Same-origin API in production: VITE_API_BASE is empty on purpose.
ENV VITE_API_BASE=""
RUN npm run build

# ── runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 STATIC_DIR=/app/static
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/artifact_hub ./artifact_hub
RUN pip install --no-cache-dir ".[vertex]" && useradd --uid 10001 --no-create-home hub
COPY --from=web /web/dist ./static
USER hub
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn artifact_hub.api:create_root_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
