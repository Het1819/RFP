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

## Authentication

The `AUTH_MODE` environment variable controls how users authenticate:

- **`dev`** — local/CI convenience mode. Any submitted email is accepted; the
  user (and a default organization) is created automatically if it does not
  exist. `AUTH_MODE=dev` is refused at startup outside `development`,
  `local`, or `test` environments.
- **`session`** — password-based login. Users must submit both a registered
  email and their password. Passwords are hashed with Argon2 (via `pwdlib`)
  and verified against the stored hash; unknown emails, inactive accounts,
  wrong passwords, and malformed stored hashes all fail with the same
  generic `Invalid email or password` message, so failures do not reveal
  account existence.
- **`oidc`** — not implemented in this MVP; the login route returns `501`.

### Provisioning a password for an existing user

Users are created via the application (dev mode) or a future admin flow, but
passwords must be set explicitly for `AUTH_MODE=session` login to work. Use
the operator script, which prompts for the password interactively and never
accepts it as a command-line argument:

```bash
uv run python scripts/set_user_password.py <user-email>
```

You will be prompted for the new password (typed input is hidden) and asked
to confirm it. Passwords must be at least 15 characters. The script updates
only an existing, unambiguous user by email — it never creates users.

### Logging out

Logout is a `POST /logout` request protected by the same CSRF token used
elsewhere in the app (submitted via a form, not a link). A `GET /logout`
request will not log the user out.

## Sessions, Expiration, and Revocation (Phase A2)

`AUTH_MODE=session` now uses **server-side, Redis-backed sessions** instead
of the Phase A1 client-side signed cookie.

### Architecture

- The browser cookie (`rfp_session` in dev/test) holds **only** a random,
  opaque session id — `secrets.token_urlsafe(32)` (256 bits), URL-safe. It
  is validated for charset/length before any Redis lookup is attempted.
  Cookie attributes: `HttpOnly`, `SameSite=Lax`, `Secure` outside
  dev/local/test, `Path=/`, no `Domain`, no `Max-Age`/`Expires` (cleared on
  browser close). A `__Host-` prefixed cookie name is supported via
  `SESSION_COOKIE_NAME` but not enabled by default in this phase.
- All session state — `user_id`, `org_id`, `csrf_token`, `created_at`,
  `last_activity_at`, `authenticated_at`, a schema version — lives
  server-side in Redis under `rfp:session:<session-id>`, as strictly
  validated JSON (never pickle). Malformed, oversized, or
  version-mismatched records are rejected and deleted.
- A per-user index at `rfp:user_sessions:<user-id>` (a Redis set) tracks
  every session belonging to a user, enabling immediate revocation.
- Login flow: visiting `/login` creates an **anonymous** server-side
  session holding only a CSRF token. On successful password verification,
  the anonymous session is deleted, a brand-new session id is generated,
  and the authenticated record (with `user_id`/`org_id`/`authenticated_at`)
  is stored under the new id. The pre-authentication id is never valid
  again.
- Implementation: `app/core/sessions/` (`models.py` for the validated
  record type, `store.py` for the `SessionStore` interface plus
  `RedisSessionStore`/`InMemorySessionStore`, `middleware.py` for
  `ServerSessionMiddleware`, `throttling.py` for login throttling).

### Idle and absolute expiration

- `SESSION_IDLE_TIMEOUT_SECONDS` (default 900 / 15 min): a session with no
  qualifying activity for this long is expired.
- `SESSION_ABSOLUTE_TIMEOUT_SECONDS` (default 28800 / 8 hours): a session
  is expired this long after it was created (or authenticated, for a
  logged-in session), regardless of activity.
- **Activity** is any request through a path other than `/static/*`,
  `/healthz`, `/health`, `/readyz`, `/metrics` — those never touch the
  session store, so they can neither refresh nor expire a session.
  Qualifying activity resets only `last_activity_at`; it never moves the
  absolute-expiry anchor.
- The Redis TTL on a session record is always `min(remaining idle,
  remaining absolute)` — but expiry is also checked explicitly against the
  stored timestamps on every request, not derived from TTL alone.
- Both timeouts are validated at startup: positive, and idle strictly
  shorter than absolute, in every environment.

### Logout and revocation

- `POST /logout` (CSRF-protected, as in A1) deletes the Redis record,
  removes it from the user's session index, and expires the cookie. It is
  idempotent — logging out twice, or with no active session, is safe.
- A cookie copied before logout stops working immediately after logout,
  because the server-side record it points to is gone.
- **Administrative revocation** — revoke every session for one account
  (e.g. suspected compromise, offboarding):
  ```bash
  uv run python scripts/revoke_user_sessions.py user@example.com
  uv run python scripts/revoke_user_sessions.py user@example.com --yes
  ```
  Looks the user up by case-insensitive exact email match (never creates
  users), shows the account and session count, requires interactive
  confirmation unless `--yes` is passed, and never prints raw session ids.

### Login throttling

`AUTH_MODE=session` enforces three independent, atomically-updated Redis
counters (no permanent lockout):

| Limit | Default |
|---|---|
| account + source IP | 5 failures / 15 min |
| source IP | 25 failures / 15 min |
| account across IPs | 20 failures / hour |
| max cooldown communicated to client | 5 min (`Retry-After`) |

The account component of every throttle key is an HMAC-SHA256 of the
normalized email (`LOGIN_THROTTLE_SECRET`) — the raw email is never stored
in a throttle key. Source IP is the direct ASGI peer address only;
`X-Forwarded-For` / `X-Real-IP` / `Forwarded` are **not** trusted in this
phase (that requires an explicit trusted-proxy list, planned for the
reverse-proxy hardening phase). A throttled response looks identical
regardless of which limit tripped or whether the account exists, and a
correct password cannot bypass an active throttle. A successful login
clears the account and account+IP counters but deliberately leaves an
abusive IP's wider history alone.

### Fail-closed behavior

If Redis is unreachable, the app **never** falls back to trusting the
browser. Any request other than `/healthz` returns `503` when the session
store can't be reached. `/healthz` stays up without Redis (pure liveness).
`/readyz` actively performs a save/get/delete round-trip against the
session store and reports `503` if it fails, independent of database
health.

### Remaining limitations (still not production-ready)

- production Compose credentials and fake-LLM configuration are unresolved;
- TLS termination and reverse-proxy hardening (including trusted-proxy-aware
  `X-Forwarded-For` handling for throttling) are not implemented;
- no multi-factor authentication (MFA) or enterprise OIDC/SSO;
- no isolation guarantees against hostile uploaded documents beyond basic
  validation;
- no evidence-backed customer-facing security documentation;
- password reset / self-service account recovery is not implemented;
- Redis data is **not** encrypted at rest or guaranteed encrypted in
  transit — do not assume TLS to Redis unless you have configured and
  verified it yourself.

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
