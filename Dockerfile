# Stage 1: Build the frontend static assets
# node:20-slim, resolved 2026-07-24 via `docker pull node:20-slim`.
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend-builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts ./
COPY app ./app
RUN npm run assets:build

# Stage 2: Build Python dependencies
# python:3.12-slim, resolved 2026-07-24 via `docker pull python:3.12-slim`.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS python-builder
# uv 0.11.24, resolved 2026-07-24 via `docker pull ghcr.io/astral-sh/uv:0.11.24`
# -- pinned to an explicit reviewed version + immutable digest, not `:latest`.
COPY --from=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_HTTP_TIMEOUT=120
# --locked: fail the build if uv.lock and pyproject.toml disagree, instead
# of silently re-resolving. --no-dev: never install the dev dependency
# group into the runtime image. --no-install-project: only install
# dependencies at this stage; app source is copied in Stage 3.
RUN uv sync --locked --no-dev --no-install-project

# Stage 3: Final lean runtime image
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runner
WORKDIR /app

# Install curl for health check utility
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create a non-root system user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy virtual environment and app code
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /app/app/static/dist /app/app/static/dist
COPY . /app

# Persistent-storage mount points: created here (not left for Docker to
# auto-create as root on first volume mount) so the non-root runtime user
# can actually write to them once docker-compose.prod.yml mounts a volume
# at these paths.
RUN mkdir -p /data/storage /data/quarantine

# Ensure correct permissions
RUN chown -R appuser:appgroup /app /data/storage /data/quarantine

# Set path and env vars
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=production
ENV PORT=8000

EXPOSE 8000

USER appuser

# Execute starting script
CMD ["bash", "scripts/start.sh"]
