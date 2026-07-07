# Production Deployment Guide

This guide explains how to build, configure, run, and maintain the RFP Architect MVP in production or pilot environments.

## 1. Required Environment Variables

Configure these variables in your target deployment context (or `.env` file):

- **`APP_ENV`**: Set to `production` or `pilot`. Dev fallbacks are automatically disabled.
- **`AUTH_MODE`**: Set to `session` (for email login) or `oidc` (for future SSO integration). **Never set to `dev` in production**.
- **`SESSION_SECRET_KEY`**: A strong, cryptographically secure string of at least 32 characters used to sign session cookies. The app will fail startup if this key is missing or weak.
- **`APP_SECRET_KEY`**: A strong secret key used for general application signing.
- **`DATABASE_URL`**: The connection string for the PostgreSQL database (e.g. `postgresql+psycopg://user:password@host:5432/dbname`).
- **`REDIS_URL`**: The connection string for Redis (e.g. `redis://host:6379/0`).

### OIDC SSO Configuration (Only if `AUTH_MODE=oidc`)
- **`OIDC_ISSUER_URL`**: The URL of your OIDC identity provider.
- **`OIDC_CLIENT_ID`**: The registered client ID.
- **`OIDC_CLIENT_SECRET`**: The client secret.
- **`OIDC_REDIRECT_URI`**: The callback URI.

### LLM Telemetry & Observability Configuration
- **`ENABLE_LLM_TELEMETRY`**: Set to `true` (default) to log LLM metadata (latencies, token counts, error types, costs) to structured logs.
- **`ENABLE_LLM_DEBUG_PAYLOAD_LOGGING`**: Must be `false` (default) in production-like environments. If enabled in dev/test, logs full payload metadata.

---

## 2. Container Build

Build the production Docker image using the multi-stage `Dockerfile`:

```bash
docker build -t rfp-architect-mvp:latest .
```

This performs a secure multi-stage build:
1. Compiles frontend assets (Vite) under a Node.js builder.
2. Resolves and compiles Python dependencies securely via `uv`.
3. Assembles a minimal, secure `python:3.12-slim` runner running as a non-root system user (`appuser`).

---

## 3. Running with Docker Compose

To test the production-like stack locally, set your environment variables (especially `SESSION_SECRET_KEY`) and run:

```bash
# Generate a strong session secret
export SESSION_SECRET_KEY=$(openssl rand -hex 32)

# Start production compose
docker compose -f docker-compose.prod.yml up -d
```

---

## 4. Database Migrations

Migrations are run automatically on container startup via `scripts/start.sh`.
To run migrations manually or in a CI/CD job without launching the main web process:

```bash
docker compose -f docker-compose.prod.yml exec app bash scripts/run_migrations.sh
```

---

## 5. Creating a Pilot User

When using `AUTH_MODE=session`, authentication requires an existing user in the database.
To create a pilot user, run the following python one-liner inside the running application container:

```bash
docker compose -f docker-compose.prod.yml exec app python -c "
from app.core.database import SessionLocal
from app.models.organization import Organization
from app.models.user import User
import uuid

db = SessionLocal()
org = db.query(Organization).first()
if not org:
    org = Organization(name='Pilot Organization')
    db.add(org)
    db.commit()
    db.refresh(org)

user = User(
    email='pilot@company.com',
    organization_id=org.id,
    hashed_password='not-applicable',
    full_name='Pilot User',
    is_active=True
)
db.add(user)
db.commit()
print('Pilot user created successfully: pilot@company.com')
"
```

---

## 6. Verifying App Status

Use the healthcheck endpoints to verify availability:

- **`/healthz`**: Returns `200` to indicate the Python process is alive.
  ```bash
  curl -f http://localhost:8000/healthz
  ```
- **`/readyz`**: Checks database connectivity and returns `200` only when the database is reachable.
  ```bash
  curl -f http://localhost:8000/readyz
  ```

---

## 7. Security Best Practices

1. **HTTPS and Reverse Proxy**: Always configure an external load balancer or reverse proxy (such as Nginx, AWS ALB, or Cloudflare) to terminate SSL/TLS before forwarding requests to the application.
2. **Backups**: Implement automated daily backups for your PostgreSQL database volume.
3. **Secrets Management**: Never bake secrets (API keys, credentials, or session keys) into Docker images. Pass them as runtime environment variables.
