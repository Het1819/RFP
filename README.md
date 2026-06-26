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

## RFP Upload Workflow (Slice 2)

### 1. Launch dev server
Ensure PostgreSQL database is running, then run the FastAPI server:
```bash
make dev
```

### 2. Navigate to projects list
Go to `http://127.0.0.1:8000/projects` to list and create proposal projects.

### 3. Open project detail
Click on a project to enter its workspace.

### 4. Upload RFP document
Upload exactly one PDF or DOCX file. Once uploaded:
- The system validates file size (Max 10MB), MIME type, extension, and content.
- A background task extracts page-by-page text.
- Live progress is displayed via HTMX polling.

## Compliance Matrix Workflow (Slice 3)
1. Navigate to **Compliance Matrix** from project detail page.
2. View extracted requirements and their classification (Section, Page, Risk, Type, Mandatory).
3. Select checkboxes and click **Merge Selected** to merge multiple requirements.
4. Click **Split** on any requirement row to split off text segments into new requirements.
5. Click **Edit** to update requirement metadata (Owner, Proposal Section, Status) inline.

## Knowledge Library & Evidence Retrieval (Slice 4)
1. On the project detail page, use the **Approved Knowledge Library** section to upload past proposal documents (PDF or DOCX).
2. Set document owner, tags, version, and approval status.
3. Click **Workspace** on any requirement in the Compliance Matrix.
4. The system automatically searches the approved knowledge library using full-text search (FTS) based on the requirement text.
5. Review evidence excerpts and click **Link Evidence** to associate them with the requirement.

## Source-Backed Draft Answers (Slice 5)
1. In the **Requirement Workspace**, click **Draft Answer (AI / Fallback)**.
2. If no evidence links are present, the system returns `NEEDS_EVIDENCE` and flags it.
3. If evidence is linked, the system uses the configured `LLMProvider` (Fake or Anthropic) to draft a source-backed response.
4. View draft response text, confidence score, and assumptions.
5. Use **Approve Answer** or **Reject Answer** to update the status.

## Review Workflow & Exports (Slice 6)
1. Assign reviewers to unresolved requirements by entering their name under **Route Gap to Reviewer**.
2. From the Compliance Matrix actions bar, export the entire requirements list to **XLSX**.
3. Export a compiled proposal draft to **DOCX** containing only approved responses.
