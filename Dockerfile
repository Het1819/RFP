# Stage 1: Build the frontend static assets
FROM node:20-slim AS frontend-builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts ./
COPY app ./app
RUN npm run assets:build

# Stage 2: Build Python dependencies
FROM python:3.12-slim AS python-builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install --no-cache -r pyproject.toml

# Stage 3: Final lean runtime image
FROM python:3.12-slim AS runner
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

# Ensure correct permissions
RUN chown -R appuser:appgroup /app

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
