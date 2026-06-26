# RFP Architect MVP — Application Foundation

RFP Architect is a human-in-the-loop proposal response workspace designed to extract compliance matrices from RFPs, retrieve verified evidence from knowledge bases, and draft source-backed answers.

This slice implements the core **Application Foundation**.

## Requirements
- Python 3.12+
- Docker & Docker Compose
- [uv](https://github.com/astral-sh/uv) (fast Python package installer/resolver)

## Local Startup

1. **Clone the repository and install dependencies:**
   ```bash
   uv sync
   ```

2. **Configure environment variables:**
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   Ensure `.env` contains correct parameters for your environment (e.g. `DATABASE_URL` pointing to PostgreSQL).

3. **Start the database and background services:**
   Ensure Docker Desktop is running, then start the containers:
   ```bash
   make up
   ```

4. **Run database migrations:**
   Apply Alembic migrations to align the database:
   ```bash
   make migrate
   ```

5. **Start the FastAPI application development server:**
   ```bash
   make dev
   ```
   Access the web interface at `http://127.0.0.1:8000/`.
   Verify API health at `http://127.0.0.1:8000/health`.

## Database Migrations

This project uses **Alembic** to manage database schema migrations.

- **Create a new migration after model changes:**
  ```bash
  uv run alembic revision --autogenerate -m "describe changes"
  ```
- **Apply migrations to head:**
  ```bash
  make migrate
  ```
- **Roll back the last migration:**
  ```bash
  uv run alembic downgrade -1
  ```

## Quality Control (Tests, Linting, & Formatting)

A single command is provided to run all tests, lints, format checks, and static typing validation:
```bash
make check
```

Alternatively, run tasks individually:

- **Run unit and integration tests:**
  ```bash
  make test
  ```
- **Lint the codebase (Ruff):**
  ```bash
  make lint
  ```
- **Format code (Ruff):**
  ```bash
  make format
  ```
- **Typecheck (mypy):**
  ```bash
  make typecheck
  ```
