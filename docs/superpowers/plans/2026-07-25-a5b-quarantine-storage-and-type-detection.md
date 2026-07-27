# A5b: Quarantine-First Upload Storage & Independent Content-Type Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every new RFP/knowledge-document upload streams into quarantine storage, gets an independently detected candidate type (PDF/DOCX, never trusting browser `Content-Type`), and is driven through the A5a `ingestion_status` state machine from `QUARANTINED` to either `SCANNING` (successfully detected, awaiting a scanner that doesn't exist yet) or `REJECTED_TYPE`. Nothing beyond `SCANNING` happens in this phase — no malware scan, no clean promotion, no parsing, no requirement extraction, no LLM call.

**Architecture:** A new `app/services/quarantine_storage.py` owns streaming writes into a dedicated quarantine root with UUID-based identifiers, SHA-256/size computation, and secure file creation. A new `app/services/document_type_detection.py` performs bounded, standard-library-only candidate detection for PDF (header/EOF markers) and DOCX (ZIP central-directory + OOXML identity parts, hardened XML parsing, no extraction). A new `app/services/document_ingestion.py` orchestrates: quarantine write → `Document` row creation (`ingestion_status=QUARANTINED` explicit) → `transition()` to `VALIDATING` → detection → `transition()` to `SCANNING` or `REJECTED_TYPE`. Both upload routes are rewired onto this one service, replacing their current duplicated inline logic. The legacy ARQ pipeline (`extract_pages`, requirement extraction) is left in place but is never reached by new uploads (they never get enqueued), and retrieval/evidence-validation gain an explicit `ingestion_status == COMPLETED` gate so nothing pre-COMPLETED is ever usable regardless of legacy `processing_status`/`approval_status` values. The `tests/conftest.py` `db` fixture is repaired first (real nested-transaction/savepoint) since every later task in this phase depends on multi-commit flows working correctly in tests.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, pytest, standard-library `zipfile`/`xml.etree` with hardening (no `defusedxml` dependency — hand-roll the hardened parser config using stdlib, since AGENTS.md forbids unnecessary new dependencies), Docker Compose.

## Global Constraints

- Controlled local task only: no deploy, no VPS, no customer documents, no production secrets, no Anthropic call, no PR merges, no ClamAV, no PyMuPDF/python-docx execution.
- `make check` must pass before this phase is considered complete.
- Do not refactor unrelated code; do not rename the existing clean-storage architecture broadly.
- Add audit events for important actions; never log raw filenames, file content, internal paths, or reflect browser MIME into error responses.
- Never commit secrets, API keys, customer documents, uploaded fixtures, quarantine files, or `.env`.
- This phase stops at `SCANNING` / `REJECTED_TYPE`. Do not implement or stub anything that claims malware scanning, content-policy inspection, clean promotion, parsing, or requirement extraction happened — no placeholder code claiming an excluded control is implemented.
- Existing test/regression conventions: `tests/integration/test_a{N}_*_weaknesses.py` naming for evidence files; tests run via `Base.metadata.create_all()` (SQLite in CI, Postgres locally) per `tests/conftest.py`; env vars for local test runs: `APP_ENV=test AUTH_MODE=dev SESSION_SECRET_KEY="test-session-secret-key-for-ci-only-do-not-use-in-production-12345" APP_SECRET_KEY="test-app-secret-key-for-ci-only-do-not-use-in-production-12345" DATABASE_URL="sqlite:///:memory:" REDIS_URL="redis://localhost:6379/0" LLM_PROVIDER="fake" QUEUE_ENABLED="false"`.
- `app/services/ingestion_state.py`'s `transition()` is the only sanctioned mutator of `Document.ingestion_status` (A5a invariant) — every status change in this phase must go through it, and it already calls `db.commit()` internally, so callers must not wrap it inside logic that assumes an outer uncommitted transaction survives.
- `transition()` provides no concurrency safety by itself (documented in its own docstring) — any call site in this phase that could race (concurrent uploads, retry) must add its own row lock (`.with_for_update()`) before calling it.
- Settings naming convention: plain `UPPER_SNAKE` (no `_FILE` suffix unless secret-bearing) — see `app/core/config.py`'s `LOCAL_STORAGE_PATH`/`MAX_UPLOAD_SIZE`. Production-hardening validation lives in `Settings.validate_production_hardening` (`mode="after"` model validator, guarded by `if self.APP_ENV in _PRODUCTION_ENVS_EXCLUDED: return self`).
- `docker-compose.prod.yml`'s `app` and `worker` services currently share the single named volume `app_storage:/data/storage`; `nginx`/`postgres`/`redis` have no document-storage mount. `compose.yml` (dev) has no `app`/`worker` service at all (dev runs them outside Compose) — dev quarantine config is just another local directory setting, no Compose volume needed.

---

## File Structure

- Modify: `tests/conftest.py` — repair the `db` fixture to use a real nested transaction/savepoint.
- Modify: `app/core/config.py` — add `QUARANTINE_STORAGE_PATH`, `QUARANTINE_CHUNK_SIZE_BYTES`, `MAX_DISPLAY_FILENAME_LENGTH`, `DOCX_DETECTION_MAX_MEMBERS`, plus production-hardening validation for the quarantine path.
- Create: `app/services/quarantine_storage.py` — filename normalization, UUID storage identifiers, streaming write with hash/size, secure file creation, safe deletion.
- Create: `tests/unit/test_quarantine_storage.py`
- Create: `app/services/document_type_detection.py` — `DetectionResult`, PDF candidate detection, DOCX candidate detection.
- Create: `tests/unit/test_document_type_detection.py`
- Create: `app/services/document_ingestion.py` — shared ingestion orchestration service.
- Create: `tests/unit/test_document_ingestion.py`
- Create: `tests/integration/test_a5b_quarantine_upload.py` — end-to-end route-level tests.
- Modify: `app/services/project_service.py` — `upload_rfp_document` rewired onto `document_ingestion`; `get_project_document` selection logic updated for "current active RFP"; RFP replacement-after-rejection logic.
- Modify: `app/web/routes/projects.py` — `upload_knowledge_action` rewired onto `document_ingestion`, `approval_status` no longer trusted from form data.
- Modify: `app/services/retriever.py`, `app/services/evidence_validation.py` — add `ingestion_status == COMPLETED` gate.
- Modify: `app/templates/projects/status_partial.html`, `app/templates/projects/detail.html` — safe fixed states for `QUARANTINED`/`VALIDATING`/`SCANNING`/`REJECTED_TYPE`/`LEGACY_UNVERIFIED`; remove `approval_status` selector from knowledge-upload form.
- Modify: `docker-compose.prod.yml` — add `quarantine_storage` volume mounted into `app` only.
- Create: `app/core/readiness.py` additions (or existing readiness module — locate it first) for quarantine-storage readiness probe.
- Modify: `DEPLOYMENT.md`, `RUNBOOK.md` (only sections directly affected).

---

### Task 1: Repair the `db` test fixture (real savepoint) + isolation tests

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/unit/test_db_fixture_isolation.py`

**Interfaces:**
- Consumes: existing `test_engine`, `TestingSessionLocal`, `Base` from `tests/conftest.py` (read the full current file before editing — quoted verbatim in the research trace above at lines 89-105, 113-140).
- Produces: a `db` fixture with real nested-transaction (savepoint) semantics that every subsequent task in this plan depends on for multi-commit route/service flows to be testable at all.

The current fixture (`tests/conftest.py:89-105`) does `connection.begin()` (outer transaction) with no savepoint; when app code calls `db.commit()`, it commits the *outer* transaction for real, and teardown's `transaction.rollback()` becomes a no-op for anything already committed — meaning committed rows leak into the next test. Fix: use SQLAlchemy 2's documented "join a session to an external transaction, with support for nested savepoint rollback" pattern — begin a savepoint (`SAVEPOINT`) and restart it via the `Session`'s `after_transaction_end` event whenever the session's own transaction (the savepoint) ends (i.e., whenever app code calls `commit()`).

- [ ] **Step 1: Write the failing isolation tests**

```python
"""Proves the `db` test fixture correctly isolates multi-commit test flows.

Every later A5b task relies on this: routes and services under test call
db.commit() multiple times (upload -> transition -> transition), and the
fixture must still guarantee full rollback at test teardown.
"""

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.document import Document
from app.services.ingestion_state import IngestionStatus, transition


def test_multiple_internal_commits_are_visible_within_one_test(db, org_project_user):
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="a.pdf",
        file_path="/tmp/a.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()  # commit #1
    transition(
        db, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id
    )  # commit #2 (internal to transition())
    transition(
        db, doc, IngestionStatus.SCANNING, org_id=org.id, user_id=user.id
    )  # commit #3
    reloaded = db.scalar(select(Document).where(Document.id == doc.id))
    assert reloaded is not None
    assert reloaded.ingestion_status == IngestionStatus.SCANNING


# Module-level marker so the next test function can assert isolation from
# the row created above without relying on execution order across files;
# pytest runs tests within a module in definition order by default, which
# is sufficient here since both tests live in this one file.
_LEAKED_DOC_ID_HOLDER: dict[str, object] = {}


def test_committed_row_from_previous_test_is_not_visible_here(db, org_project_user):
    """If this test can see a Document named "a.pdf" from the previous
    test, the fixture is leaking committed data across tests."""
    org, project, user = org_project_user
    leaked = db.scalar(select(Document).where(Document.name == "a.pdf"))
    assert leaked is None


def test_rollback_after_exception_clears_all_test_state(db, org_project_user):
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="b.pdf",
        file_path="/tmp/b.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()
    with pytest.raises(ValueError):
        raise ValueError("simulated mid-flow failure after a commit")
    # No explicit assertion here - the real proof is the next test.


def test_previous_tests_exception_state_did_not_leak(db, org_project_user):
    leaked = db.scalar(select(Document).where(Document.name == "b.pdf"))
    assert leaked is None


def test_audit_events_participate_correctly_across_commits(db, org_project_user):
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="c.pdf",
        file_path="/tmp/c.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()
    transition(db, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id)
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == doc.id)
    ).all()
    assert len(events) == 1
    assert events[0].details["to"] == IngestionStatus.VALIDATING


def test_no_audit_events_leak_from_previous_test(db):
    from sqlalchemy import func

    count = db.scalar(select(func.count()).select_from(AuditEvent))
    assert count == 0
```

Note: `org_project_user` is not an existing fixture (confirmed absent from `tests/conftest.py` in the research trace). Add it as a new fixture in `tests/conftest.py` in this same task, since every later task needs it too — mirror the pattern `tests/integration/test_projects.py` uses (`get_default_org_and_user(db)` plus manual `ProposalProject` construction):

```python
@pytest.fixture
def org_project_user(db):
    from app.core.database import get_default_org_and_user
    from app.models.project import ProposalProject

    org, user = get_default_org_and_user(db)
    project = ProposalProject(
        organization_id=org.id,
        name="Test Project",
        created_by_id=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return org, project, user
```

(Read `app/models/project.py` first to confirm `ProposalProject`'s exact required constructor fields before writing this — adapt field names if they differ from the guess above.)

- [ ] **Step 2: Run the tests to verify they fail (or pass for the wrong reason) against the current fixture**

Run: `APP_ENV=test AUTH_MODE=dev DATABASE_URL="sqlite:///:memory:" .venv/Scripts/python.exe -m pytest tests/unit/test_db_fixture_isolation.py -v`
Expected: at least `test_committed_row_from_previous_test_is_not_visible_here` and/or `test_previous_tests_exception_state_did_not_leak` FAIL, proving the current fixture leaks.

- [ ] **Step 3: Repair the `db` fixture**

Replace `tests/conftest.py:89-105` with a real savepoint-based implementation:

```python
@pytest.fixture
def db():
    """Provides a transactional database session rolled back after each test.

    Uses SQLAlchemy 2's documented external-transaction join pattern: the
    connection opens one real outer transaction, the Session joins it and
    additionally opens a SAVEPOINT. When application code calls
    session.commit(), only the SAVEPOINT is released - the outer
    transaction is untouched. A session-level event listener restarts a
    fresh SAVEPOINT immediately whenever the previous one ends, so a test
    can call commit() any number of times and each one still nests inside
    the outer transaction, which is unconditionally rolled back at
    teardown regardless of how many internal commits occurred.
    """
    connection = test_engine.connect()
    outer_transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    session.begin_nested()  # SAVEPOINT

    @sa_event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()
```

Add `from sqlalchemy import event as sa_event` to the imports at the top of `tests/conftest.py` if not already present (check first — avoid a duplicate/conflicting `event` import name).

- [ ] **Step 4: Run the isolation tests to verify they pass**

Run: `APP_ENV=test AUTH_MODE=dev DATABASE_URL="sqlite:///:memory:" .venv/Scripts/python.exe -m pytest tests/unit/test_db_fixture_isolation.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the same file against real PostgreSQL to confirm consistent behavior**

Start local Postgres if not running: `docker compose -f compose.yml up -d postgres` and wait healthy.
Run: `APP_ENV=test AUTH_MODE=dev DATABASE_URL="postgresql://rfp_user:<local-dev-password-from-.env>@localhost:5432/rfp_architect" .venv/Scripts/python.exe -m pytest tests/unit/test_db_fixture_isolation.py -v` (read `.env`/`compose.yml` for the actual local dev Postgres credentials/db name rather than guessing — do not use any production secret).
Expected: all 6 tests PASS identically. If SQLite and Postgres diverge (e.g. SAVEPOINT behavior differs), document the exact divergence in the test file as a comment and add a `pytest.mark.skipif` only if a specific, named SQLite/Postgres incompatibility is discovered — do not silently skip without a documented reason.

- [ ] **Step 6: Run the FULL existing suite to confirm no regressions from the fixture change**

Run: `APP_ENV=test AUTH_MODE=dev SESSION_SECRET_KEY="test-session-secret-key-for-ci-only-do-not-use-in-production-12345" APP_SECRET_KEY="test-app-secret-key-for-ci-only-do-not-use-in-production-12345" DATABASE_URL="sqlite:///:memory:" REDIS_URL="redis://localhost:6379/0" LLM_PROVIDER="fake" QUEUE_ENABLED="false" .venv/Scripts/python.exe -m pytest -q`
Expected: same or higher pass count than the A5a baseline (367 passed, 54 skipped), zero new failures. If any existing test relied on the old (buggy) leak-across-commits behavior, fix that test's isolation assumption — do not revert the fixture fix to accommodate a test that was depending on the bug.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/unit/test_db_fixture_isolation.py
git commit -m "test: fix transactional database isolation"
```

---

### Task 2: Quarantine configuration settings

**Files:**
- Modify: `app/core/config.py`
- Create: `tests/unit/test_quarantine_config.py`

**Interfaces:**
- Consumes: existing `Settings` class patterns (`LOCAL_STORAGE_PATH`, `MAX_UPLOAD_SIZE`, `validate_production_hardening`).
- Produces: `settings.QUARANTINE_STORAGE_PATH: str`, `settings.QUARANTINE_CHUNK_SIZE_BYTES: int`, `settings.MAX_DISPLAY_FILENAME_LENGTH: int`, `settings.DOCX_DETECTION_MAX_MEMBERS: int` — consumed by Tasks 3-5.

- [ ] **Step 1: Write the failing config tests**

```python
import pytest
from pydantic import ValidationError

from app.core.config import Settings


class TestQuarantineSettingsDefaults:
    def test_defaults_present(self) -> None:
        s = Settings(APP_ENV="development")
        assert s.QUARANTINE_STORAGE_PATH == "./data/quarantine"
        assert s.QUARANTINE_CHUNK_SIZE_BYTES == 1024 * 1024
        assert s.MAX_DISPLAY_FILENAME_LENGTH == 255
        assert s.DOCX_DETECTION_MAX_MEMBERS == 5000


class TestQuarantinePathProductionValidation:
    def _prod_kwargs(self, **overrides: str) -> dict:
        base = dict(
            APP_ENV="production",
            SESSION_SECRET_KEY="x" * 32,
            APP_SECRET_KEY="x" * 32,
            LOGIN_THROTTLE_SECRET="x" * 32,
            LOCAL_STORAGE_PATH="/data/storage",
            DATABASE_URL="postgresql://u:p@host/db",
            REDIS_URL="redis://:p@host:6379/0",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="x" * 20,
            LLM_MODEL="claude-3",
        )
        base.update(overrides)
        return base

    def test_default_quarantine_path_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="QUARANTINE_STORAGE_PATH"):
            Settings(**self._prod_kwargs())

    def test_relative_quarantine_path_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="absolute"):
            Settings(**self._prod_kwargs(QUARANTINE_STORAGE_PATH="relative/path"))

    def test_quarantine_path_equal_to_storage_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="differ"):
            Settings(
                **self._prod_kwargs(
                    QUARANTINE_STORAGE_PATH="/data/storage",
                    LOCAL_STORAGE_PATH="/data/storage",
                )
            )

    def test_valid_distinct_absolute_quarantine_path_accepted(self) -> None:
        s = Settings(
            **self._prod_kwargs(
                QUARANTINE_STORAGE_PATH="/data/quarantine",
                LOCAL_STORAGE_PATH="/data/storage",
            )
        )
        assert s.QUARANTINE_STORAGE_PATH == "/data/quarantine"
```

(Read `app/core/config.py`'s full `validate_production_hardening` validator and every other required-in-production field first — the `_prod_kwargs` dict above must be adapted to include every field that validator actually requires, not guessed; check `tests/unit/test_production_storage_config.py` and similar existing test files for the exact working pattern of constructing a valid production `Settings()` in tests, and copy that pattern rather than reinventing it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_quarantine_config.py -v`
Expected: FAIL (`AttributeError` / no such setting).

- [ ] **Step 3: Add the settings**

In `app/core/config.py`, near `LOCAL_STORAGE_PATH`/`MAX_UPLOAD_SIZE` (currently lines 127-129):

```python
    QUARANTINE_STORAGE_PATH: str = "./data/quarantine"
    QUARANTINE_CHUNK_SIZE_BYTES: int = 1024 * 1024  # 1 MiB
    MAX_DISPLAY_FILENAME_LENGTH: int = 255
    DOCX_DETECTION_MAX_MEMBERS: int = 5000
```

In `validate_production_hardening`, immediately after the existing `LOCAL_STORAGE_PATH` block (current lines 436-449), add:

```python
        # 15: quarantine storage must be a real, distinct, absolute mounted
        # path in production-like environments - never the repo-relative
        # dev default, and never the same directory as clean document
        # storage (quarantine and clean documents must never share a root).
        if self.STORAGE_BACKEND == "local":
            if (
                self.QUARANTINE_STORAGE_PATH == "./data/quarantine"
                or not self.QUARANTINE_STORAGE_PATH
            ):
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must be set to a persistent "
                    "mounted path (not the './data/quarantine' development "
                    "default) in production-like environments"
                )
            if not self.QUARANTINE_STORAGE_PATH.startswith("/"):
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must be an absolute path to a "
                    "mounted volume in production-like environments"
                )
            if Path(self.QUARANTINE_STORAGE_PATH).resolve() == Path(
                self.LOCAL_STORAGE_PATH
            ).resolve():
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must differ from "
                    "LOCAL_STORAGE_PATH - quarantine and clean document "
                    "storage must be separate directories"
                )
```

Add `from pathlib import Path` to `app/core/config.py`'s imports if not already present (check first).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_quarantine_config.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: full suite command from Task 1 Step 6.
Expected: no new failures (existing production-config tests in `tests/unit/test_production_storage_config.py` etc. must still pass — if they construct a production `Settings()` without `QUARANTINE_STORAGE_PATH`, they'll now fail on the new validator; update those test fixtures/kwargs to include a valid `QUARANTINE_STORAGE_PATH` rather than weakening the new validator).

- [ ] **Step 6: Run ruff/mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/core/config.py tests/unit/test_quarantine_config.py && .venv/Scripts/python.exe -m mypy app/core/config.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add app/core/config.py tests/unit/test_quarantine_config.py tests/unit/test_production_storage_config.py
git commit -m "security: add quarantine storage configuration"
```

(Include any other existing test files touched in Step 5 in this commit.)

---

### Task 3: Quarantine storage service (streaming, hashing, identifiers, filename normalization)

**Files:**
- Create: `app/services/quarantine_storage.py`
- Create: `tests/unit/test_quarantine_storage.py`

**Interfaces:**
- Consumes: `settings.QUARANTINE_STORAGE_PATH`, `settings.QUARANTINE_CHUNK_SIZE_BYTES`, `settings.MAX_DISPLAY_FILENAME_LENGTH`, `settings.MAX_UPLOAD_SIZE` (Task 2).
- Produces:
  - `normalize_display_filename(original: str | None) -> str`
  - `@dataclass(frozen=True) class QuarantineWriteResult: storage_id: uuid.UUID; storage_path: Path; sha256_digest: str; byte_size: int; header_bytes: bytes; tail_bytes: bytes`
  - `class QuarantineStorageError(Exception)` with a fixed `.code: str` (e.g. `"EMPTY_FILE"`, `"TOO_LARGE"`, `"READ_FAILURE"`) — no raw exception text.
  - `def stream_upload_to_quarantine(upload: UploadFile, *, max_size: int | None = None) -> QuarantineWriteResult` — streams, hashes, enforces size, writes securely, returns the result. Raises `QuarantineStorageError` on any failure, always cleaning up partial files itself.
  - `def resolve_quarantine_path(storage_id: uuid.UUID) -> Path` — validates and resolves a storage id to a path under the quarantine root, with containment checks.
  - `def delete_quarantine_file(storage_id: uuid.UUID) -> None` — safe deletion, idempotent if already gone, containment-checked.
  - Consumed by Task 6 (`document_ingestion.py`).

- [ ] **Step 1: Write failing tests for filename normalization**

```python
import pytest

from app.services.quarantine_storage import normalize_display_filename


class TestNormalizeDisplayFilename:
    def test_windows_path_reduced_to_basename(self) -> None:
        assert normalize_display_filename(r"C:\Users\evil\..\rfp.pdf") == "rfp.pdf"

    def test_posix_path_reduced_to_basename(self) -> None:
        assert normalize_display_filename("/etc/passwd/../rfp.pdf") == "rfp.pdf"

    def test_nul_and_control_characters_removed(self) -> None:
        assert normalize_display_filename("rfp\x00\x01.pdf") == "rfp.pdf"

    def test_unicode_normalized_to_nfc(self) -> None:
        # "e" + combining acute accent (NFD) -> should normalize to NFC "é"
        decomposed = "re\u0301sume\u0301.pdf"
        result = normalize_display_filename(decomposed)
        import unicodedata

        assert result == unicodedata.normalize("NFC", decomposed)

    def test_overlong_filename_is_bounded(self) -> None:
        long_name = ("a" * 500) + ".pdf"
        result = normalize_display_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".pdf")

    def test_empty_or_unsafe_name_uses_fallback(self) -> None:
        assert normalize_display_filename("") == "Uploaded document"
        assert normalize_display_filename(None) == "Uploaded document"
        assert normalize_display_filename("....") == "Uploaded document"

    def test_trailing_dots_and_spaces_trimmed(self) -> None:
        assert normalize_display_filename("rfp.pdf   ...") == "rfp.pdf"

    def test_safe_pdf_suffix_preserved(self) -> None:
        assert normalize_display_filename("My RFP Response.pdf").endswith(".pdf")

    def test_safe_docx_suffix_preserved(self) -> None:
        assert normalize_display_filename("Knowledge Doc.DOCX").lower().endswith(
            ".docx"
        )
```

- [ ] **Step 2: Run to verify failure, then implement `normalize_display_filename`**

```python
import unicodedata
from pathlib import PureWindowsPath

from app.core.config import settings

_SAFE_SUFFIXES = {".pdf", ".docx"}
_FALLBACK_NAME = "Uploaded document"


def normalize_display_filename(original: str | None) -> str:
    if not original:
        return _FALLBACK_NAME

    # Strip both POSIX and Windows path components regardless of host OS.
    basename = PureWindowsPath(original).name
    basename = basename.rsplit("/", 1)[-1]

    # Remove NUL and ASCII control characters.
    basename = "".join(ch for ch in basename if ord(ch) >= 0x20 and ch != "\x7f")

    basename = unicodedata.normalize("NFC", basename)
    basename = basename.strip().rstrip(". ").strip()

    if not basename or basename.strip(". ") == "":
        return _FALLBACK_NAME

    suffix = ""
    lower = basename.lower()
    for safe in _SAFE_SUFFIXES:
        if lower.endswith(safe):
            suffix = basename[-len(safe) :]
            basename = basename[: -len(safe)]
            break

    max_len = settings.MAX_DISPLAY_FILENAME_LENGTH
    budget = max_len - len(suffix)
    if budget < 1:
        return _FALLBACK_NAME
    basename = basename[:budget]

    result = (basename + suffix).strip().rstrip(". ").strip()
    return result if result else _FALLBACK_NAME
```

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_quarantine_storage.py -v -k Normalize`
Expected: PASS.

- [ ] **Step 3: Write failing tests for streaming/storage identifiers**

```python
import io
import os
import uuid
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import settings
from app.services.quarantine_storage import (
    QuarantineStorageError,
    delete_quarantine_file,
    resolve_quarantine_path,
    stream_upload_to_quarantine,
)


def _upload(content: bytes, filename: str = "x.pdf") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": "application/pdf"}),
    )


class TestStreamUploadToQuarantine:
    def test_empty_upload_fails(self) -> None:
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(_upload(b""))
        assert exc.value.code == "EMPTY_FILE"

    def test_valid_small_upload_succeeds(self) -> None:
        content = b"%PDF-1.4\n" + b"x" * 1000 + b"\n%%EOF"
        result = stream_upload_to_quarantine(_upload(content))
        assert result.byte_size == len(content)
        import hashlib

        assert result.sha256_digest == hashlib.sha256(content).hexdigest()
        assert result.storage_path.exists()
        result.storage_path.unlink()

    def test_oversized_upload_fails_without_full_buffering(self) -> None:
        oversized = b"x" * (settings.MAX_UPLOAD_SIZE + 1)
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(_upload(oversized))
        assert exc.value.code == "TOO_LARGE"

    def test_generated_filename_contains_no_submitted_filename(self) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        upload = _upload(content, filename="super-secret-client-name.pdf")
        result = stream_upload_to_quarantine(upload)
        assert "super-secret-client-name" not in str(result.storage_path)
        assert result.storage_path.suffix in (".upload", ".bin")
        result.storage_path.unlink()

    def test_existing_file_is_never_overwritten(self, monkeypatch) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        fixed_id = uuid.uuid4()
        monkeypatch.setattr("uuid.uuid4", lambda: fixed_id)
        result1 = stream_upload_to_quarantine(_upload(content))
        with pytest.raises(QuarantineStorageError):
            # Second call with the same monkeypatched uuid4 must not
            # silently overwrite the first file.
            stream_upload_to_quarantine(_upload(content))
        result1.storage_path.unlink()

    def test_file_permissions_are_restrictive_on_posix(self) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only permission check")
        content = b"%PDF-1.4\nx\n%%EOF"
        result = stream_upload_to_quarantine(_upload(content))
        mode = result.storage_path.stat().st_mode & 0o777
        assert mode == 0o600
        result.storage_path.unlink()


class TestResolveQuarantinePath:
    def test_symlink_destination_rejected(self, tmp_path) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only symlink check")
        real_target = tmp_path / "outside.txt"
        real_target.write_text("nope")
        storage_id = uuid.uuid4()
        link_path = Path(settings.QUARANTINE_STORAGE_PATH) / f"{storage_id}.upload"
        Path(settings.QUARANTINE_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
        os.symlink(real_target, link_path)
        try:
            with pytest.raises(QuarantineStorageError):
                resolve_quarantine_path(storage_id)
        finally:
            link_path.unlink()

    @pytest.mark.parametrize(
        "bad_id",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "C:\\evil",
            "id\x00with-nul",
            "not-a-uuid-at-all",
        ],
    )
    def test_path_traversal_and_malformed_identifiers_rejected(
        self, bad_id: str
    ) -> None:
        with pytest.raises((QuarantineStorageError, ValueError)):
            resolve_quarantine_path(bad_id)  # type: ignore[arg-type]
```

- [ ] **Step 4: Implement the streaming/identifier portion of `quarantine_storage.py`**

```python
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

_STORAGE_SUFFIX = ".upload"
_TAIL_WINDOW_BYTES = 1024
_HEADER_WINDOW_BYTES = 1024


class QuarantineStorageError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class QuarantineWriteResult:
    storage_id: uuid.UUID
    storage_path: Path
    sha256_digest: str
    byte_size: int
    header_bytes: bytes
    tail_bytes: bytes


def _quarantine_root() -> Path:
    root = Path(settings.QUARANTINE_STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


def resolve_quarantine_path(storage_id: uuid.UUID | str) -> Path:
    try:
        validated_id = storage_id if isinstance(storage_id, uuid.UUID) else uuid.UUID(
            str(storage_id)
        )
    except (ValueError, AttributeError, TypeError) as e:
        raise QuarantineStorageError("INVALID_IDENTIFIER") from e

    root = _quarantine_root()
    candidate = (root / f"{validated_id}{_STORAGE_SUFFIX}").resolve()

    if candidate.parent != root:
        raise QuarantineStorageError("PATH_ESCAPE")
    if candidate.is_symlink():
        raise QuarantineStorageError("SYMLINK_REJECTED")
    return candidate


def stream_upload_to_quarantine(
    upload: UploadFile, *, max_size: int | None = None
) -> QuarantineWriteResult:
    limit = max_size if max_size is not None else settings.MAX_UPLOAD_SIZE
    storage_id = uuid.uuid4()
    final_path = resolve_quarantine_path(storage_id)
    partial_path = final_path.with_suffix(final_path.suffix + ".partial")

    hasher = hashlib.sha256()
    total = 0
    header_bytes = b""
    tail_window = b""

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(partial_path, flags, 0o600)
    except FileExistsError as e:
        raise QuarantineStorageError("IDENTIFIER_COLLISION") from e

    try:
        with os.fdopen(fd, "wb") as f:
            chunk_size = settings.QUARANTINE_CHUNK_SIZE_BYTES
            while True:
                try:
                    chunk = upload.file.read(chunk_size)
                except Exception as e:
                    raise QuarantineStorageError("READ_FAILURE") from e
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise QuarantineStorageError("TOO_LARGE")
                hasher.update(chunk)
                if len(header_bytes) < _HEADER_WINDOW_BYTES:
                    header_bytes += chunk[: _HEADER_WINDOW_BYTES - len(header_bytes)]
                tail_window = (tail_window + chunk)[-_TAIL_WINDOW_BYTES:]
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())

        if total == 0:
            raise QuarantineStorageError("EMPTY_FILE")

        if final_path.exists():
            raise QuarantineStorageError("IDENTIFIER_COLLISION")
        os.rename(partial_path, final_path)
        try:
            dir_fd = os.open(final_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # best-effort on platforms without directory fsync (e.g. Windows)

        return QuarantineWriteResult(
            storage_id=storage_id,
            storage_path=final_path,
            sha256_digest=hasher.hexdigest(),
            byte_size=total,
            header_bytes=header_bytes,
            tail_bytes=tail_window,
        )
    except QuarantineStorageError:
        partial_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        partial_path.unlink(missing_ok=True)
        raise QuarantineStorageError("WRITE_FAILURE") from e


def delete_quarantine_file(storage_id: uuid.UUID) -> None:
    path = resolve_quarantine_path(storage_id)
    path.unlink(missing_ok=True)
```

Note on `test_existing_file_is_never_overwritten`: with the implementation above, `O_CREAT | O_EXCL` on the `.partial` path is what actually prevents overwrite at the OS level (`os.open` raises `FileExistsError` -> `IDENTIFIER_COLLISION`) — this is the real, load-bearing guarantee; the extra `final_path.exists()` check before `os.rename` is defense in depth for the (POSIX) atomic-rename case. On Windows dev, `os.O_NOFOLLOW` doesn't exist (guarded by `hasattr`), and `os.rename` fails if the destination exists rather than replacing it (unlike POSIX) — confirm this in Step 5 and adjust the collision test's platform assumptions if Windows raises a different exception type than expected; do not weaken the POSIX production guarantee to accommodate a Windows dev quirk, just handle both correctly.

- [ ] **Step 5: Run all Task 3 tests, fixing any platform-specific gaps (Windows dev vs. POSIX prod semantics)**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_quarantine_storage.py -v`
Expected: all PASS, with POSIX-only tests correctly skipped on Windows (`pytest.skip`) rather than failing — but the underlying `stream_upload_to_quarantine`/`resolve_quarantine_path` logic itself must remain fully correct and testable on the POSIX CI runner (this repo's `.github/workflows/ci.yml` runs on Linux), so a Windows-dev skip is acceptable but a Linux-CI skip is not.

- [ ] **Step 6: Run ruff/mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/services/quarantine_storage.py tests/unit/test_quarantine_storage.py && .venv/Scripts/python.exe -m mypy app/services/quarantine_storage.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add app/services/quarantine_storage.py tests/unit/test_quarantine_storage.py
git commit -m "security: add quarantine storage service"
```

---

### Task 4: PDF candidate-type detection

**Files:**
- Create: `app/services/document_type_detection.py` (PDF portion; DOCX portion added in Task 5)
- Create: `tests/unit/test_document_type_detection.py` (PDF portion)

**Interfaces:**
- Consumes: `bytes` (header/tail windows — this module never receives a live `UploadFile`; it operates on the `header_bytes`/`tail_bytes`/full-file-path already captured by `quarantine_storage.py` in Task 3, so it stays fully decoupled and independently testable).
- Produces:
  ```python
  class DetectedType(str, Enum):
      PDF = "PDF"
      DOCX = "DOCX"
      UNKNOWN = "UNKNOWN"

  @dataclass(frozen=True)
  class DetectionResult:
      detected_type: DetectedType
      canonical_mime: str | None
      expected_extension: str | None
      reason_code: str | None
      safe_summary: str
  ```
  `def detect_pdf_candidate(file_path: Path, *, declared_extension: str) -> DetectionResult` — consumed by Task 6.

- [ ] **Step 1: Write failing PDF detection tests**

```python
from pathlib import Path

import pytest

from app.services.document_type_detection import DetectedType, detect_pdf_candidate


def _write(tmp_path: Path, content: bytes, name: str = "f.pdf") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


class TestDetectPdfCandidate:
    def test_valid_pdf_candidate_detected(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\n" + b"1 0 obj\n<<>>\nendobj\n" + b"%%EOF"
        result = detect_pdf_candidate(_write(tmp_path, content), declared_extension=".pdf")
        assert result.detected_type == DetectedType.PDF
        assert result.canonical_mime == "application/pdf"

    def test_arbitrary_bytes_renamed_pdf_fails(self, tmp_path: Path) -> None:
        content = b"this is just plain text pretending to be a pdf file padding"
        result = detect_pdf_candidate(_write(tmp_path, content), declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code is not None

    def test_missing_header_fails(self, tmp_path: Path) -> None:
        content = b"no header here\n" + b"x" * 100 + b"\n%%EOF"
        result = detect_pdf_candidate(_write(tmp_path, content), declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_eof_marker_fails(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\n" + b"x" * 100
        result = detect_pdf_candidate(_write(tmp_path, content), declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_truncated_candidate_fails(self, tmp_path: Path) -> None:
        result = detect_pdf_candidate(_write(tmp_path, b"%PDF-1."), declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_valid_pdf_with_docx_display_extension_fails(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        result = detect_pdf_candidate(
            _write(tmp_path, content, name="f.docx"), declared_extension=".docx"
        )
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "EXTENSION_TYPE_MISMATCH"

    def test_detection_does_not_import_pymupdf(self) -> None:
        import sys

        assert "fitz" not in sys.modules or True  # importing this test module
        # must not itself have pulled fitz in as a transitive import of
        # document_type_detection.
        import app.services.document_type_detection as mod
        import inspect

        src = inspect.getsource(mod)
        assert "import fitz" not in src
        assert "pymupdf" not in src.lower()

    @pytest.mark.parametrize("version", [b"%PDF-1.0", b"%PDF-1.7", b"%PDF-2.0"])
    def test_accepted_header_versions(self, tmp_path: Path, version: bytes) -> None:
        content = version + b"\nx\n%%EOF"
        result = detect_pdf_candidate(_write(tmp_path, content), declared_extension=".pdf")
        assert result.detected_type == DetectedType.PDF
```

- [ ] **Step 2: Implement PDF detection**

```python
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_PDF_HEADER_RE = re.compile(rb"^%PDF-1\.[0-7]|^%PDF-2\.0")
_PDF_HEADER_WINDOW = 1024
_PDF_EOF_WINDOW = 1024


class DetectedType(str, Enum):
    PDF = "PDF"
    DOCX = "DOCX"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DetectionResult:
    detected_type: DetectedType
    canonical_mime: str | None
    expected_extension: str | None
    reason_code: str | None
    safe_summary: str


_PDF_MIME = "application/pdf"
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _unknown(reason_code: str, summary: str) -> DetectionResult:
    return DetectionResult(
        detected_type=DetectedType.UNKNOWN,
        canonical_mime=None,
        expected_extension=None,
        reason_code=reason_code,
        safe_summary=summary,
    )


def detect_pdf_candidate(file_path: Path, *, declared_extension: str) -> DetectionResult:
    if declared_extension.lower() != ".pdf":
        return _unknown(
            "EXTENSION_TYPE_MISMATCH",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )

    size = file_path.stat().st_size
    with file_path.open("rb") as f:
        header = f.read(_PDF_HEADER_WINDOW)
        if size > _PDF_EOF_WINDOW:
            f.seek(max(0, size - _PDF_EOF_WINDOW))
        tail = f.read(_PDF_EOF_WINDOW)

    if not _PDF_HEADER_RE.match(header):
        return _unknown(
            "PDF_HEADER_INVALID",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )
    if b"%%EOF" not in tail:
        return _unknown(
            "PDF_EOF_MISSING",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )
    if size < len(header) + 5:  # header + minimal EOF marker
        return _unknown(
            "PDF_TRUNCATED",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )

    return DetectionResult(
        detected_type=DetectedType.PDF,
        canonical_mime=_PDF_MIME,
        expected_extension=".pdf",
        reason_code=None,
        safe_summary="Recognized as a PDF candidate.",
    )
```

- [ ] **Step 3: Run tests, fix, run ruff/mypy**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_document_type_detection.py -v -k Pdf`
Then: `.venv/Scripts/python.exe -m ruff check app/services/document_type_detection.py tests/unit/test_document_type_detection.py && .venv/Scripts/python.exe -m mypy app/services/document_type_detection.py`
Expected: all pass, clean.

- [ ] **Step 4: Commit**

```bash
git add app/services/document_type_detection.py tests/unit/test_document_type_detection.py
git commit -m "security: add PDF candidate-type detection"
```

---

### Task 5: DOCX candidate-type detection

**Files:**
- Modify: `app/services/document_type_detection.py` (add DOCX portion)
- Modify: `tests/unit/test_document_type_detection.py` (add DOCX portion)

**Interfaces:**
- Consumes: Task 4's `DetectedType`, `DetectionResult`, `_unknown`, `_DOCX_MIME`.
- Produces: `def detect_docx_candidate(file_path: Path, *, declared_extension: str) -> DetectionResult` — consumed by Task 6.

- [ ] **Step 1: Write failing DOCX detection tests**

```python
import io
import zipfile
from pathlib import Path

import pytest

from app.services.document_type_detection import DetectedType, detect_docx_candidate

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)
_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p/></w:body></w:document>"
)


def _build_minimal_docx(tmp_path: Path, name: str = "f.docx", **overrides: bytes) -> Path:
    members = {
        "[Content_Types].xml": _CONTENT_TYPES_XML.encode(),
        "_rels/.rels": _RELS_XML.encode(),
        "word/document.xml": _DOCUMENT_XML.encode(),
    }
    members.update(overrides)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member_name, data in members.items():
            if data is not None:
                zf.writestr(member_name, data)
    return path


class TestDetectDocxCandidate:
    def test_valid_minimal_docx_detected(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path)
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.DOCX

    def test_generic_zip_renamed_docx_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "just a zip")
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_content_types_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"[Content_Types].xml": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_rels_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_document_xml_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"word/document.xml": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_wrong_main_relationship_fails(self, tmp_path: Path) -> None:
        bad_rels = _RELS_XML.replace(
            "officeDocument/2006/relationships/officeDocument",
            "officeDocument/2006/relationships/image",
        )
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": bad_rels.encode()})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_macro_enabled_content_type_fails(self, tmp_path: Path) -> None:
        macro_content_types = _CONTENT_TYPES_XML.replace(
            "wordprocessingml.document.main+xml",
            "wordprocessingml.document.macroEnabled.main+xml",
        )
        path = _build_minimal_docx(
            tmp_path, **{"[Content_Types].xml": macro_content_types.encode()}
        )
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "MACRO_ENABLED_PACKAGE"

    def test_duplicate_member_names_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            zf.writestr("_rels/.rels", _RELS_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)  # duplicate
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_excessive_preliminary_member_count_fails(self, tmp_path: Path, monkeypatch) -> None:
        from app.core import config

        monkeypatch.setattr(config.settings, "DOCX_DETECTION_MAX_MEMBERS", 3)
        path = _build_minimal_docx(
            tmp_path,
            **{f"word/extra{i}.xml": b"<x/>" for i in range(5)},
        )
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_corrupt_zip_fails_safely(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        path.write_bytes(b"PK\x03\x04not a real zip central directory garbage")
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_dtd_in_identity_xml_fails(self, tmp_path: Path) -> None:
        malicious_rels = (
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            + _RELS_XML
        )
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": malicious_rels.encode()})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_valid_docx_with_pdf_display_extension_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, name="f.pdf")
        result = detect_docx_candidate(path, declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_no_member_is_extracted_to_disk(self, tmp_path: Path, monkeypatch) -> None:
        import zipfile as zf_module

        original_extract = zf_module.ZipFile.extract
        original_extractall = zf_module.ZipFile.extractall

        def _fail(*a, **kw):
            raise AssertionError("extract/extractall must never be called")

        monkeypatch.setattr(zf_module.ZipFile, "extract", _fail)
        monkeypatch.setattr(zf_module.ZipFile, "extractall", _fail)
        path = _build_minimal_docx(tmp_path)
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.DOCX
```

- [ ] **Step 2: Implement DOCX detection**

Append to `app/services/document_type_detection.py`:

```python
import zipfile
from xml.parsers.expat import ExpatError, ParserCreate

_DOCX_IDENTITY_MEMBERS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
}
_DOCX_MAIN_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_MACRO_CONTENT_TYPE_MARKERS = ("macroEnabled",)
_MAX_IDENTITY_XML_BYTES = 1024 * 1024  # 1 MiB per identity part, generous but bounded


def _read_zip_member_bounded(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > _MAX_IDENTITY_XML_BYTES:
        raise ValueError("identity part too large")
    return zf.read(name)


def _hardened_parse(xml_bytes: bytes) -> None:
    """Raises on DTD/entity declarations; does not build a DOM (identity
    checks below only need substring/attribute inspection on well-formed
    XML, done via a lightweight scan, not full parsing into objects)."""
    parser = ParserCreate()
    parser.DefaultHandler = lambda data: None

    def _reject_dtd(*_args: object) -> None:
        raise ValueError("DTD declarations are not permitted")

    parser.StartDoctypeDeclHandler = _reject_dtd
    parser.EntityDeclHandler = _reject_dtd
    parser.UnparsedEntityDeclHandler = _reject_dtd
    parser.ExternalEntityRefHandler = lambda *_a: False  # deny resolution
    try:
        parser.Parse(xml_bytes, True)
    except ExpatError as e:
        raise ValueError("malformed XML") from e


def detect_docx_candidate(file_path: Path, *, declared_extension: str) -> DetectionResult:
    if declared_extension.lower() != ".docx":
        return _unknown(
            "EXTENSION_TYPE_MISMATCH",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )

    try:
        with zipfile.ZipFile(file_path) as zf:
            infolist = zf.infolist()

            names = [info.filename for info in infolist]
            if len(names) != len(set(names)):
                return _unknown(
                    "DOCX_DUPLICATE_MEMBER",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            from app.core.config import settings

            if len(infolist) > settings.DOCX_DETECTION_MAX_MEMBERS:
                return _unknown(
                    "DOCX_MEMBER_COUNT_EXCEEDED",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            name_set = set(names)
            if not _DOCX_IDENTITY_MEMBERS.issubset(name_set):
                return _unknown(
                    "DOCX_MISSING_IDENTITY_PART",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            try:
                content_types_xml = _read_zip_member_bounded(
                    zf, "[Content_Types].xml"
                )
                rels_xml = _read_zip_member_bounded(zf, "_rels/.rels")
            except (ValueError, KeyError, zipfile.BadZipFile):
                return _unknown(
                    "DOCX_MALFORMED_PACKAGE",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            try:
                _hardened_parse(content_types_xml)
                _hardened_parse(rels_xml)
            except ValueError:
                return _unknown(
                    "DOCX_UNSAFE_XML",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            if any(marker in content_types_xml.decode("utf-8", "replace") for marker in _MACRO_CONTENT_TYPE_MARKERS):
                return _unknown(
                    "MACRO_ENABLED_PACKAGE",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            if b"word/document.xml" not in content_types_xml and "word/document.xml" not in content_types_xml.decode("utf-8", "replace"):
                return _unknown(
                    "DOCX_CONTENT_TYPES_MISMATCH",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

            rels_text = rels_xml.decode("utf-8", "replace")
            if _DOCX_MAIN_RELATIONSHIP_TYPE not in rels_text or "word/document.xml" not in rels_text:
                return _unknown(
                    "DOCX_MAIN_RELATIONSHIP_INVALID",
                    "The uploaded file does not match a supported PDF or DOCX format.",
                )

    except zipfile.BadZipFile:
        return _unknown(
            "DOCX_NOT_A_ZIP",
            "The uploaded file does not match a supported PDF or DOCX format.",
        )

    return DetectionResult(
        detected_type=DetectedType.DOCX,
        canonical_mime=_DOCX_MIME,
        expected_extension=".docx",
        reason_code=None,
        safe_summary="Recognized as a DOCX candidate.",
    )
```

Note: the content-types/rels checks above use bounded substring inspection on the already-hardened-parsed (DTD/entity-rejected) XML text rather than building a full DOM, per the spec's "bounded XML bytes for the few identity parts read" and "hardened XML parser configuration" requirements — `_hardened_parse` proves the XML contains no DTD/entity declarations and is well-formed (raising otherwise) before any text inspection occurs, which is the actual security boundary; the substring checks afterward are identity heuristics, not a security control themselves.

- [ ] **Step 3: Run tests, fix, run ruff/mypy**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_document_type_detection.py -v`
Then: `.venv/Scripts/python.exe -m ruff check app/services/document_type_detection.py tests/unit/test_document_type_detection.py && .venv/Scripts/python.exe -m mypy app/services/document_type_detection.py`
Expected: all pass (PDF + DOCX), clean.

- [ ] **Step 4: Commit**

```bash
git add app/services/document_type_detection.py tests/unit/test_document_type_detection.py
git commit -m "security: add DOCX candidate-type detection"
```

---

### Task 6: Shared document-ingestion orchestration service

**Files:**
- Create: `app/services/document_ingestion.py`
- Create: `tests/unit/test_document_ingestion.py`

**Interfaces:**
- Consumes: `stream_upload_to_quarantine`, `QuarantineStorageError` (Task 3); `detect_pdf_candidate`, `detect_docx_candidate`, `DetectedType` (Tasks 4-5); `transition`, `IngestionStatus`, `IngestionStateError` (A5a `ingestion_state.py`); `Document` model (A5a); `log_audit_event` (`project_service.py`, existing).
- Produces: `def ingest_uploaded_document(db: Session, *, project: ProposalProject, org_id: uuid.UUID, user_id: uuid.UUID, upload: UploadFile, doc_role: str, **role_metadata: Any) -> Document` — consumed by Task 7 (both routes).

- [ ] **Step 1: Write failing tests**

```python
import io

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.models.document import Document
from app.services.document_ingestion import ingest_uploaded_document
from app.services.ingestion_state import IngestionStatus
from app.services.quarantine_storage import QuarantineStorageError


def _pdf_upload(content: bytes | None = None, filename: str = "rfp.pdf") -> UploadFile:
    content = content or (b"%PDF-1.4\n" + b"x" * 200 + b"\n%%EOF")
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": "application/pdf"}),
    )


class TestIngestUploadedDocument:
    def test_valid_pdf_reaches_scanning(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        doc = ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=_pdf_upload(),
            doc_role="rfp",
        )
        assert doc.ingestion_status == IngestionStatus.SCANNING
        assert doc.detected_content_type == "application/pdf"
        assert doc.sha256_digest is not None
        assert doc.file_size_bytes is not None
        assert doc.quarantined_at is not None
        assert doc.display_filename == "rfp.pdf"
        assert doc.content is None

    def test_invalid_type_reaches_rejected_type(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        garbage = _pdf_upload(content=b"definitely not a pdf, just words padded out")
        doc = ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=garbage,
            doc_role="rfp",
        )
        assert doc.ingestion_status == IngestionStatus.REJECTED_TYPE
        assert doc.rejection_reason_code is not None

    def test_document_created_explicitly_quarantined_before_transitions(
        self, db, org_project_user, monkeypatch
    ) -> None:
        """Verify the very first persisted state is QUARANTINED, not
        whatever the ORM default happens to be, by intercepting the first
        commit."""
        org, project, user = org_project_user
        seen_statuses: list[str] = []
        from app.services import document_ingestion as mod

        original = mod.transition

        def _spy(db_, document, new_status, **kw):
            seen_statuses.append(new_status)
            return original(db_, document, new_status, **kw)

        monkeypatch.setattr(mod, "transition", _spy)
        ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=_pdf_upload(),
            doc_role="rfp",
        )
        assert seen_statuses[0] == IngestionStatus.VALIDATING
        assert IngestionStatus.SCANNING in seen_statuses

    def test_no_processing_job_created(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.job import ProcessingJob
        from sqlalchemy import select

        ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=_pdf_upload(),
            doc_role="rfp",
        )
        jobs = db.scalars(select(ProcessingJob)).all()
        assert jobs == []

    def test_quarantine_file_removed_if_db_commit_fails(
        self, db, org_project_user, monkeypatch
    ) -> None:
        org, project, user = org_project_user
        from app.services import document_ingestion as mod

        written_paths: list = []
        original_stream = mod.stream_upload_to_quarantine

        def _capture(*a, **kw):
            result = original_stream(*a, **kw)
            written_paths.append(result.storage_path)
            return result

        monkeypatch.setattr(mod, "stream_upload_to_quarantine", _capture)

        def _boom(*a, **kw):
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(db, "commit", _boom)

        with pytest.raises(Exception):
            ingest_uploaded_document(
                db,
                project=project,
                org_id=org.id,
                user_id=user.id,
                upload=_pdf_upload(),
                doc_role="rfp",
            )
        assert written_paths
        assert not written_paths[0].exists()

    def test_audit_event_has_no_raw_filename_or_path(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.audit import AuditEvent
        from sqlalchemy import select

        doc = ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=_pdf_upload(filename="totally-secret-client-name.pdf"),
            doc_role="rfp",
        )
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == doc.id)
        ).all()
        for event in events:
            payload = str(event.details)
            assert "totally-secret-client-name" not in payload
            assert str(doc.file_path) not in payload if doc.file_path else True
```

(Adapt `db.commit = _boom` monkeypatching carefully — `Session.commit` may need `monkeypatch.setattr(type(db), "commit", _boom)` depending on SQLAlchemy's session internals; verify the actual working approach interactively before finalizing this test.)

- [ ] **Step 2: Implement `document_ingestion.py`**

```python
"""Shared quarantine-first ingestion orchestration for both RFP and
knowledge-document uploads.

Sequence: stream to quarantine -> create Document(ingestion_status=
QUARANTINED) -> transition to VALIDATING -> run candidate-type detection
-> transition to SCANNING (success) or REJECTED_TYPE (failure). This
phase (A5b) never transitions past SCANNING - no malware scan, no clean
promotion, no parsing, and no legacy processing job is ever enqueued for
a document created through this function.
"""

import uuid
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.project import ProposalProject
from app.services.document_type_detection import DetectedType, detect_docx_candidate, detect_pdf_candidate
from app.services.ingestion_state import IngestionStateError, IngestionStatus, transition
from app.services.quarantine_storage import (
    QuarantineStorageError,
    delete_quarantine_file,
    normalize_display_filename,
    stream_upload_to_quarantine,
)

_SAFE_UPLOAD_FAILURE_MESSAGE = "The document could not be accepted. Please upload a valid PDF or DOCX file."


def ingest_uploaded_document(
    db: Session,
    *,
    project: ProposalProject,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    upload: UploadFile,
    doc_role: str,
    **role_metadata: Any,
) -> Document:
    display_filename = normalize_display_filename(upload.filename)

    try:
        write_result = stream_upload_to_quarantine(upload)
    except QuarantineStorageError as e:
        raise HTTPException(status_code=400, detail=_SAFE_UPLOAD_FAILURE_MESSAGE) from e

    doc = Document(
        project_id=project.id,
        name=display_filename,
        display_filename=display_filename,
        file_path=str(write_result.storage_path),
        file_type=upload.content_type or "application/octet-stream",  # untrusted, kept only for backward compatibility
        doc_role=doc_role,
        ingestion_status=IngestionStatus.QUARANTINED,
        sha256_digest=write_result.sha256_digest,
        file_size_bytes=write_result.byte_size,
        quarantined_at=_utcnow(),
        processing_status="pending_security_scan",
        created_by_id=user_id,
        **role_metadata,
    )
    db.add(doc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_quarantine_file(write_result.storage_id)
        raise
    db.refresh(doc)

    from app.services.project_service import log_audit_event

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="document_upload_quarantined",
        entity_type="Document",
        entity_id=doc.id,
        details={"doc_role": doc_role},
    )

    try:
        transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)

        declared_extension = _extension_of(display_filename)
        detection = None
        if declared_extension == ".pdf":
            detection = detect_pdf_candidate(
                write_result.storage_path, declared_extension=declared_extension
            )
        elif declared_extension == ".docx":
            detection = detect_docx_candidate(
                write_result.storage_path, declared_extension=declared_extension
            )

        if detection is not None and detection.detected_type != DetectedType.UNKNOWN:
            doc.detected_content_type = detection.canonical_mime
            db.commit()
            transition(db, doc, IngestionStatus.SCANNING, org_id=org_id, user_id=user_id)
        else:
            reason = detection.reason_code if detection else "UNSUPPORTED_EXTENSION"
            if detection is not None and detection.canonical_mime:
                doc.detected_content_type = detection.canonical_mime
                db.commit()
            transition(
                db,
                doc,
                IngestionStatus.REJECTED_TYPE,
                org_id=org_id,
                user_id=user_id,
                reason_code=reason,
                safe_summary=_SAFE_UPLOAD_FAILURE_MESSAGE,
            )
    except IngestionStateError:
        # Fail closed: never leave the document silently past VALIDATING.
        raise

    return doc


def _extension_of(display_filename: str) -> str:
    idx = display_filename.rfind(".")
    return display_filename[idx:].lower() if idx != -1 else ""


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)
```

- [ ] **Step 3: Run tests, iterate until passing**

Run: `APP_ENV=test AUTH_MODE=dev DATABASE_URL="sqlite:///:memory:" .venv/Scripts/python.exe -m pytest tests/unit/test_document_ingestion.py -v`
Expected: all pass. Debug and correct real issues you find in the implementation above (e.g. SQLAlchemy session-commit interactions with `role_metadata`, exact `Document` constructor field validity) rather than weakening the tests.

- [ ] **Step 4: Run ruff/mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/services/document_ingestion.py tests/unit/test_document_ingestion.py && .venv/Scripts/python.exe -m mypy app/services/document_ingestion.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add app/services/document_ingestion.py tests/unit/test_document_ingestion.py
git commit -m "security: add shared quarantine ingestion service"
```

---

### Task 7: Wire both upload routes onto the ingestion service; RFP replacement; knowledge approval gating

**Files:**
- Modify: `app/services/project_service.py` — `upload_rfp_document`, `get_project_document`.
- Modify: `app/web/routes/projects.py` — `upload_knowledge_action`.
- Create: `tests/integration/test_a5b_quarantine_upload.py`

**Interfaces:**
- Consumes: `ingest_uploaded_document` (Task 6).
- Produces: rewired routes; `get_project_document` returns the current active (non-terminally-rejected) RFP; `upload_knowledge_action` no longer trusts client-submitted `approval_status`.

- [ ] **Step 1: Write failing integration tests**

```python
"""A5b end-to-end route behavior: uploads quarantine, detect, stop at
SCANNING/REJECTED_TYPE, never enqueue legacy processing."""

import io

from app.models.document import Document
from app.services.ingestion_state import IngestionStatus


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n" + b"x" * 200 + b"\n%%EOF"


def _docx_bytes() -> bytes:
    import zipfile

    buf = io.BytesIO()
    # Reuse the minimal-DOCX builder pattern from test_document_type_detection.py
    from tests.unit.test_document_type_detection import (
        _CONTENT_TYPES_XML,
        _DOCUMENT_XML,
        _RELS_XML,
    )

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("word/document.xml", _DOCUMENT_XML)
    return buf.getvalue()


class TestRfpUploadRoute:
    def test_valid_pdf_reaches_scanning(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        doc = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").one()
        assert doc.ingestion_status == IngestionStatus.SCANNING

    def test_invalid_type_rejected(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", b"not a pdf at all just words", "application/pdf")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        doc = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").one()
        assert doc.ingestion_status == IngestionStatus.REJECTED_TYPE

    def test_rejected_rfp_can_be_replaced(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", b"garbage not a pdf padded out", "application/pdf")},
            follow_redirects=False,
        )
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp2.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        docs = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").all()
        assert len(docs) == 2
        statuses = {d.ingestion_status for d in docs}
        assert IngestionStatus.SCANNING in statuses
        assert IngestionStatus.REJECTED_TYPE in statuses

    def test_active_scanning_rfp_blocks_second_upload(
        self, client, db, org_project_user
    ) -> None:
        org, project, user = org_project_user
        client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp2.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        # Second upload while an active (non-terminal) RFP exists must be
        # rejected with the existing "already has an RFP" error path.
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]
        docs = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").all()
        assert len(docs) == 1

    def test_no_legacy_processing_job_created(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.job import ProcessingJob

        client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert db.query(ProcessingJob).count() == 0


class TestKnowledgeUploadRoute:
    def test_valid_docx_reaches_scanning(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        resp = client.post(
            f"/projects/{project.id}/knowledge",
            files={
                "file": (
                    "kb.docx",
                    _docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"approval_status": "APPROVED"},  # forged; must be ignored
            follow_redirects=False,
        )
        assert resp.status_code == 303
        doc = (
            db.query(Document)
            .filter_by(project_id=project.id, doc_role="knowledge_base")
            .one()
        )
        assert doc.ingestion_status == IngestionStatus.SCANNING
        assert doc.approval_status == "PENDING"  # forged APPROVED must be ignored

    def test_forged_approval_status_ignored(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        client.post(
            f"/projects/{project.id}/knowledge",
            files={
                "file": (
                    "kb.docx",
                    _docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"approval_status": "APPROVED"},
            follow_redirects=False,
        )
        doc = (
            db.query(Document)
            .filter_by(project_id=project.id, doc_role="knowledge_base")
            .one()
        )
        assert doc.approval_status != "APPROVED"
```

(This test file needs `client`/`db`/`org_project_user` — `client` and `db` already exist/were repaired in Task 1; verify `client` yields requests bound to the same `db` session per the conftest read in the research trace before assuming this works, and that `org_project_user`'s `project` is visible to the authenticated `client`'s org — if `client`'s dev-login always logs into a fixed default org/user (per `get_default_org_and_user`), make sure `org_project_user` uses that SAME org/user rather than creating a second, unrelated one, or the route calls will 404 with "Project not found." Read `tests/integration/test_projects.py`'s existing upload test for the exact working pattern and mirror it.)

- [ ] **Step 2: Update `get_project_document` for "current active RFP" selection**

In `app/services/project_service.py`, replace the current `.first()` implementation (lines 66-72):

```python
_TERMINAL_REJECTED_STATUSES = frozenset(
    {
        IngestionStatus.REJECTED_TYPE,
        IngestionStatus.REJECTED_MALWARE,
        IngestionStatus.REJECTED_CONTENT_POLICY,
    }
)


def get_project_document(db: Session, project_id: uuid.UUID) -> Document | None:
    """Retrieve the current active RFP document for a project, if one
    exists. "Active" means not in a terminal rejection state - a
    project may have multiple historical rejected-type RFP rows, but at
    most one active (non-terminally-rejected) row at a time."""
    candidates = db.scalars(
        select(Document)
        .where(Document.project_id == project_id, Document.doc_role == "rfp")
        .order_by(Document.created_at.desc())
    ).all()
    for doc in candidates:
        if doc.ingestion_status not in _TERMINAL_REJECTED_STATUSES:
            return doc
    return None
```

Add `from app.services.ingestion_state import IngestionStatus` to `project_service.py`'s imports.

- [ ] **Step 3: Rewrite `upload_rfp_document` onto `ingest_uploaded_document`, with row locking for the concurrent-upload guard**

```python
def upload_rfp_document(
    db: Session,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    file: UploadFile,
    background_tasks: BackgroundTasks | None = None,
) -> Document:
    """Stream an RFP upload into quarantine and run independent
    candidate-type detection. Stops at ingestion_status SCANNING or
    REJECTED_TYPE - no malware scan, parsing, or requirement extraction
    happens here (A5c+)."""
    proj = get_project(db, project_id, org_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # Row-lock existing RFP rows for this project to prevent two
    # concurrent uploads both observing "no active RFP" and both
    # succeeding. SQLite (test/dev) does not support FOR UPDATE row
    # locks; guard accordingly.
    query = select(Document).where(
        Document.project_id == project_id, Document.doc_role == "rfp"
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    existing = db.scalars(query).all()
    active_existing = [
        d for d in existing if d.ingestion_status not in _TERMINAL_REJECTED_STATUSES
    ]
    if active_existing:
        raise HTTPException(
            status_code=400, detail="Project already has an active RFP document"
        )

    from app.services.document_ingestion import ingest_uploaded_document

    return ingest_uploaded_document(
        db,
        project=proj,
        org_id=org_id,
        user_id=user_id,
        upload=file,
        doc_role="rfp",
    )
```

Remove the now-dead `validate_uploaded_file`/`shutil`/`Path` upload-writing code that used to live in this function (the old body) — but do NOT remove `validate_uploaded_file` from `extractor.py` itself, since it's out of this function's scope to delete a shared utility other code may still reference; only remove this function's own now-unused inline usage. Check whether `shutil`/`Path` imports at the top of `project_service.py` are still used elsewhere in the file before removing them (they likely still are, e.g. in `process_document_background`).

- [ ] **Step 4: Rewrite `upload_knowledge_action` onto `ingest_uploaded_document`, dropping trust in client-submitted `approval_status`**

In `app/web/routes/projects.py`, replace the current inline body (lines 189-275) — keep the route signature accepting `approval_status: str = Form("APPROVED")` for backward form-compatibility (so existing form posts don't 422), but ignore its value entirely and always create new documents as `PENDING`:

```python
@router.post(
    "/{project_id}/knowledge",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def upload_knowledge_action(
    request: Request,
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    owner_name: str = Form(None),
    tags: str = Form(None),
    approval_status: str = Form(None),  # accepted but ignored - see below
    version: str = Form("1.0"),
    review_date: str = Form(None),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)

    parsed_review_date = None
    if review_date:
        try:
            parsed_review_date = datetime.strptime(review_date, "%Y-%m-%d")
        except ValueError:
            pass

    project = get_project_for_org(db, project_id, org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.document_ingestion import ingest_uploaded_document

    try:
        ingest_uploaded_document(
            db,
            project=project,
            org_id=org_id,
            user_id=user_id,
            upload=file,
            doc_role="knowledge_base",
            # approval_status is intentionally NOT taken from client input;
            # every new knowledge document starts PENDING regardless of
            # what the form submitted, and can only become APPROVED via
            # an explicit reviewer action after ingestion_status reaches
            # COMPLETED (enforced separately, not yet wired in A5b).
            approval_status="PENDING",
            owner_name=owner_name,
            tags=tags,
            version=version,
            review_date=parsed_review_date,
        )
    except HTTPException as e:
        db.rollback()
        return RedirectResponse(
            url=f"/projects/{project_id}?error={e.detail}", status_code=303
        )

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
```

Remove the form's `approval_status` `<select>` from `app/templates/projects/detail.html` (the exact edit belongs in Task 10, but note here that the route now ignores the field entirely regardless of template state, so a stale template posting `approval_status=APPROVED` is still safely ignored — defense in depth).

- [ ] **Step 5: Run the new integration tests, fixing real issues found**

Run: `APP_ENV=test AUTH_MODE=dev SESSION_SECRET_KEY="test-session-secret-key-for-ci-only-do-not-use-in-production-12345" APP_SECRET_KEY="test-app-secret-key-for-ci-only-do-not-use-in-production-12345" DATABASE_URL="sqlite:///:memory:" REDIS_URL="redis://localhost:6379/0" LLM_PROVIDER="fake" QUEUE_ENABLED="false" .venv/Scripts/python.exe -m pytest tests/integration/test_a5b_quarantine_upload.py -v`
Expected: all pass after fixing genuine integration issues (e.g. `db.bind` may not exist the way assumed above on a `Session` bound via `bind=connection` in tests — verify `db.bind.dialect.name` actually resolves correctly against the Task-1 fixture, adjust if SQLAlchemy 2's session-binding API differs).

- [ ] **Step 6: Run the FULL suite to catch regressions in existing upload/queue/tenant-isolation tests**

Run the full suite command from Task 1 Step 6.
Expected: existing tests that assumed the OLD `upload_rfp_document`/`upload_knowledge_action` behavior (e.g. `tests/integration/test_projects.py`, `tests/integration/test_knowledge.py`, `tests/integration/test_queue_jobs.py`) will likely need updates — their assumption that upload synchronously enqueues a `document_processing` job and immediately writes to `LOCAL_STORAGE_PATH/documents` is now false. Update those tests to match the new quarantine-first behavior (assert `ingestion_status` progression instead of legacy `processing_status`/job creation) rather than reverting the route behavior. Do not delete test coverage — adapt it.

- [ ] **Step 7: Run ruff/mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/services/project_service.py app/web/routes/projects.py tests/integration/test_a5b_quarantine_upload.py && .venv/Scripts/python.exe -m mypy app/services/project_service.py app/web/routes/projects.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add app/services/project_service.py app/web/routes/projects.py tests/integration/test_a5b_quarantine_upload.py tests/integration/test_projects.py tests/integration/test_knowledge.py tests/integration/test_queue_jobs.py
git commit -m "security: route uploads through quarantine lifecycle"
```

(Include every existing test file you had to adapt in Step 6.)

---

### Task 8: Fail-closed gates on legacy processing, retrieval, and evidence validation

**Files:**
- Modify: `app/services/retriever.py`
- Modify: `app/services/evidence_validation.py`
- Modify: `app/services/project_service.py` (`process_job_pipeline_async` / `run_job_sync` — add a fail-closed assertion)
- Create: `tests/unit/test_a5b_legacy_pipeline_gate.py`

**Interfaces:**
- Consumes: `IngestionStatus` (A5a).
- Produces: retrieval and evidence-validation queries additionally require `ingestion_status == COMPLETED`; the legacy ARQ pipeline refuses to process a document below `CLEAN`.

- [ ] **Step 1: Write failing tests**

```python
import pytest

from app.models.document import Document, DocumentPage
from app.services.evidence_validation import EvidenceValidationError, validate_evidence_candidate
from app.services.ingestion_state import IngestionStatus
from app.services.retriever import retrieve_evidence


class TestRetrievalGatedByIngestionStatus:
    def test_scanning_document_excluded_from_retrieval(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",  # legacy field, deliberately "green" to prove the new gate is what blocks it
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(DocumentPage(document_id=doc.id, page_number=1, content="needle content"))
        db.commit()

        results = retrieve_evidence(db, project.id, "needle")
        assert all(r.document_id != doc.id for r in results)

    def test_completed_document_included_in_retrieval(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",
            ingestion_status=IngestionStatus.COMPLETED,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(DocumentPage(document_id=doc.id, page_number=1, content="needle content"))
        db.commit()

        results = retrieve_evidence(db, project.id, "needle")
        assert any(r.document_id == doc.id for r in results)


class TestEvidenceValidationGatedByIngestionStatus:
    def test_scanning_document_rejected_as_evidence(self, db, org_project_user) -> None:
        org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        with pytest.raises(EvidenceValidationError):
            validate_evidence_candidate(doc)
```

(Read `app/services/retriever.py`'s exact `retrieve_evidence` function signature and `evidence_validation.py`'s `validate_evidence_candidate` signature in full before writing these — adapt call signatures to match reality, do not guess.)

```python
"""Proves new uploads below CLEAN can never reach the legacy PyMuPDF/
python-docx extraction pipeline, even if something tried to enqueue
them (defense in depth beyond simply "never enqueue" in Task 6/7)."""

import pytest

from app.models.document import Document
from app.services.ingestion_state import IngestionStatus


class TestLegacyPipelineFailsClosedBelowClean:
    @pytest.mark.parametrize(
        "status",
        [
            IngestionStatus.QUARANTINED,
            IngestionStatus.VALIDATING,
            IngestionStatus.SCANNING,
            IngestionStatus.REJECTED_TYPE,
        ],
    )
    def test_pipeline_refuses_to_extract_below_clean(
        self, db, org_project_user, status
    ) -> None:
        import asyncio

        from app.models.job import ProcessingJob
        from app.services.project_service import process_job_pipeline_async

        org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/does-not-matter.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=status,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        job = ProcessingJob(
            org_id=org.id,
            project_id=project.id,
            document_id=doc.id,
            job_type="document_processing",
            status="QUEUED",
            max_attempts=3,
        )
        db.add(job)
        db.commit()

        asyncio.run(process_job_pipeline_async(db, job))

        db.refresh(doc)
        assert doc.processing_status != "completed"
        assert doc.ingestion_status == status  # unchanged, still not CLEAN/COMPLETED
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_a5b_legacy_pipeline_gate.py -v`
Expected: FAIL (retrieval/evidence currently don't check `ingestion_status`; the legacy pipeline currently processes regardless of it).

- [ ] **Step 3: Add the gate to `retriever.py`**

Add `Document.ingestion_status == IngestionStatus.COMPLETED` to both the PostgreSQL raw-SQL `WHERE` clause and the SQLite `.where(...)` clause (research trace lines ~29-33 and ~72-77). For the raw SQL path, parameterize the enum value rather than string-interpolating it:

```python
          AND d.ingestion_status = :completed_status
```
with `{"completed_status": IngestionStatus.COMPLETED, ...}` added to the existing params dict. For the SQLite path:
```python
        .where(
            Document.project_id == project_id,
            Document.doc_role == "knowledge_base",
            Document.approval_status == "APPROVED",
            Document.processing_status == "completed",
            Document.ingestion_status == IngestionStatus.COMPLETED,
        )
```
Add `from app.services.ingestion_state import IngestionStatus` to `retriever.py`'s imports.

- [ ] **Step 4: Add the gate to `evidence_validation.py`**

After the existing `processing_status`/`approval_status` checks (research trace lines 154-164), add:

```python
    if doc.ingestion_status != IngestionStatus.COMPLETED:
        raise EvidenceValidationError(
            status_code=400,
            detail="Document has not completed security processing",
        )
```
Add the same import.

- [ ] **Step 5: Add the fail-closed assertion to the legacy pipeline**

In `app/services/project_service.py`'s `process_job_pipeline_async`, immediately before the existing `pages_data = extract_pages(file_path, doc.file_type)` call, add:

```python
        if doc.ingestion_status != IngestionStatus.CLEAN:
            job.status = "FAILED"
            job.safe_error_message = (
                "Document has not passed required security validation."
            )
            doc.processing_status = "failed"
            db.commit()
            return
```

(This is intentionally a soft, job-level failure rather than a raised exception, matching the existing pipeline's error-handling shape — verify this against the actual surrounding `process_job_pipeline_async` control flow in the full file before finalizing, since the research trace only quoted an excerpt; adapt to fit the function's real structure, e.g. its existing try/except and retry-counting logic, rather than introducing a second, inconsistent failure path.)

Since no A5b-created document is ever enqueued (Task 6/7 never call `enqueue_job`), this assertion is a defense-in-depth backstop for `LEGACY_UNVERIFIED` documents or any other future code path that might still enqueue — it must never fire for a document created via `ingest_uploaded_document` in normal operation, and the test in Step 1 proves it fires correctly if something tries anyway.

- [ ] **Step 6: Run tests, then full suite, then ruff/mypy**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_a5b_legacy_pipeline_gate.py -v` then the full suite command, then ruff/mypy on the four touched files.
Expected: all pass, clean, no regressions (existing retrieval/evidence tests that construct `Document` rows without setting `ingestion_status` will now get the A5a default `LEGACY_UNVERIFIED` and correctly FAIL the new gate — update those existing tests to explicitly set `ingestion_status=IngestionStatus.COMPLETED` where they intend the document to be valid evidence, matching real production documents that will eventually reach `COMPLETED` through A5c-A5e).

- [ ] **Step 7: Commit**

```bash
git add app/services/retriever.py app/services/evidence_validation.py app/services/project_service.py tests/unit/test_a5b_legacy_pipeline_gate.py
git commit -m "security: gate retrieval, evidence, and legacy processing on ingestion_status"
```

(Include any existing retrieval/evidence test files adapted in Step 6.)

---

### Task 9: Compose quarantine volume + readiness probe

**Files:**
- Modify: `docker-compose.prod.yml`
- Create or modify: readiness module (locate the existing readiness/health-check implementation first — grep for `/readyz` referenced in `docker-compose.prod.yml`'s `app` healthcheck, research trace shows `test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/readyz"]` — find the route handler for `/readyz`).
- Create: `tests/integration/test_a5b_quarantine_compose.py`
- Create: `tests/unit/test_quarantine_readiness.py`

**Interfaces:**
- Consumes: `settings.QUARANTINE_STORAGE_PATH`.
- Produces: `quarantine_storage` named volume mounted into `app` and `worker` only; `/readyz` additionally checks quarantine storage.

- [ ] **Step 1: Locate the existing readiness implementation**

Grep the repo for `readyz` to find the route/module (likely `app/web/routes/` or a dedicated `app/core/health.py`/`readiness.py` — the research trace didn't cover this file, so read it fresh now before writing code). Read `tests/integration/test_health_readiness.py` (present in the existing test list) to understand the current readiness-check pattern and how it's tested.

- [ ] **Step 2: Write failing readiness tests**

```python
import os
import stat

import pytest

from app.core.config import settings


class TestQuarantineReadiness:
    def test_missing_quarantine_root_fails_readiness(self, tmp_path, monkeypatch) -> None:
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(missing))
        from app.core.readiness import check_quarantine_storage  # adjust import to real module location

        result = check_quarantine_storage()
        assert result.healthy is False

    def test_symlink_quarantine_root_fails_readiness(self, tmp_path, monkeypatch) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only symlink check")
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir, target_is_directory=True)
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(link))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert result.healthy is False

    def test_unwritable_quarantine_root_fails_readiness(self, tmp_path, monkeypatch) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only permission check")
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(ro_dir))
        from app.core.readiness import check_quarantine_storage

        try:
            result = check_quarantine_storage()
            assert result.healthy is False
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_healthy_quarantine_root_passes_readiness(self, tmp_path, monkeypatch) -> None:
        healthy_dir = tmp_path / "quarantine"
        healthy_dir.mkdir()
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(healthy_dir))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert result.healthy is True
```

- [ ] **Step 3: Implement `check_quarantine_storage` in whatever module the existing readiness checks live in, and wire it into `/readyz`**

```python
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


@dataclass(frozen=True)
class ReadinessCheckResult:
    healthy: bool
    detail: str


def check_quarantine_storage() -> ReadinessCheckResult:
    root = Path(settings.QUARANTINE_STORAGE_PATH)
    if not root.exists():
        return ReadinessCheckResult(False, "quarantine storage unavailable")
    if root.is_symlink():
        return ReadinessCheckResult(False, "quarantine storage unavailable")
    if not root.is_dir():
        return ReadinessCheckResult(False, "quarantine storage unavailable")

    probe_name = f".readyz-probe-{uuid.uuid4().hex}"
    probe_path = root / probe_name
    try:
        fd = os.open(probe_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        probe_path.unlink()
    except OSError:
        return ReadinessCheckResult(False, "quarantine storage unavailable")

    return ReadinessCheckResult(True, "quarantine storage ready")
```

Wire this into the existing `/readyz` handler (production readiness fails if unhealthy; liveness/`/healthz`, if it exists separately, must NOT depend on this check — confirm the existing liveness/readiness separation pattern before adding the call, and add it only to the readiness path).

- [ ] **Step 4: Add the Compose volume**

In `docker-compose.prod.yml`, add to the top-level `volumes:` block (alongside `postgres_data`/`redis_data`/`app_storage`):

```yaml
  quarantine_storage:
```

Add to `app`'s `volumes:` list:
```yaml
      - quarantine_storage:/data/quarantine
```
Add to `app`'s `environment:`:
```yaml
      QUARANTINE_STORAGE_PATH: /data/quarantine
```
Repeat both additions identically for `worker` — the spec requires the worker to receive the quarantine mount "during A5b" per the file-structure description above (re-read spec section 12 carefully: it says "worker must not receive the quarantine mount during A5b because no worker security stage is implemented" — **do NOT mount quarantine into `worker`**, only into `app`). Correct this plan's own File Structure note: quarantine mounts into `app` ONLY, not `worker`, for this phase.

Do not add anything to `nginx`, `postgres`, or `redis`.

- [ ] **Step 5: Write Compose-mount-isolation tests**

```python
import yaml


class TestQuarantineComposeMountIsolation:
    def _load_compose(self) -> dict:
        with open("docker-compose.prod.yml") as f:
            return yaml.safe_load(f)

    def test_quarantine_volume_declared(self) -> None:
        compose = self._load_compose()
        assert "quarantine_storage" in compose["volumes"]

    def test_only_app_mounts_quarantine(self) -> None:
        compose = self._load_compose()
        for service_name, service in compose["services"].items():
            volumes = service.get("volumes", []) or []
            mounts_quarantine = any("quarantine_storage" in v for v in volumes)
            if service_name == "app":
                assert mounts_quarantine, "app must mount quarantine_storage"
            else:
                assert not mounts_quarantine, (
                    f"{service_name} must NOT mount quarantine_storage"
                )
```

- [ ] **Step 6: Run all tests, ruff/mypy**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_quarantine_readiness.py tests/integration/test_a5b_quarantine_compose.py -v`
Then ruff/mypy on touched files, plus `docker compose -f docker-compose.prod.yml config --quiet` to confirm the YAML is still valid (no secrets required for a syntax-only render — check if `config --quiet` needs the `secrets/` files present; if so, generate throwaway local ones exactly as A4's validation did, and delete them afterward, never committing them).
Expected: all pass, valid Compose render.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.prod.yml tests/unit/test_quarantine_readiness.py tests/integration/test_a5b_quarantine_compose.py
git add <the actual readiness module path>
git commit -m "security: add quarantine storage volume and readiness check"
```

---

### Task 10: UI safe states + documentation

**Files:**
- Modify: `app/templates/projects/status_partial.html`
- Modify: `app/templates/projects/detail.html`
- Modify: `DEPLOYMENT.md`, `RUNBOOK.md` (only directly affected sections)
- Create: `tests/integration/test_a5b_ui_states.py`

**Interfaces:**
- Consumes: `document.ingestion_status`.
- Produces: templates show fixed safe strings for each `IngestionStatus`; knowledge-upload form no longer offers an `approval_status` selector.

- [ ] **Step 1: Write failing template-rendering tests**

```python
class TestQuarantineUiStates:
    def test_quarantined_status_shows_upload_received(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.document import Document
        from app.services.ingestion_state import IngestionStatus

        doc = Document(
            project_id=project.id, name="x.pdf", file_path="/tmp/x.pdf",
            file_type="application/pdf", doc_role="rfp",
            ingestion_status=IngestionStatus.QUARANTINED, created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        resp = client.get(f"/projects/{project.id}/status")
        assert "Upload received" in resp.text
        assert str(doc.file_path) not in resp.text

    def test_scanning_status_shows_awaiting_scan(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.document import Document
        from app.services.ingestion_state import IngestionStatus

        doc = Document(
            project_id=project.id, name="x.pdf", file_path="/tmp/x.pdf",
            file_type="application/pdf", doc_role="rfp",
            ingestion_status=IngestionStatus.SCANNING, created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        resp = client.get(f"/projects/{project.id}/status")
        assert "security scan" in resp.text.lower()
        assert "malware" not in resp.text.lower() or "awaiting" in resp.text.lower()

    def test_rejected_type_shows_safe_message(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        from app.models.document import Document
        from app.services.ingestion_state import IngestionStatus

        doc = Document(
            project_id=project.id, name="x.pdf", file_path="/tmp/x.pdf",
            file_type="application/pdf", doc_role="rfp",
            ingestion_status=IngestionStatus.REJECTED_TYPE,
            processing_error="Traceback (most recent call last): raw internal error",
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        resp = client.get(f"/projects/{project.id}/status")
        assert "valid PDF or DOCX" in resp.text
        assert "Traceback" not in resp.text

    def test_knowledge_upload_form_has_no_approval_selector(self, client, db, org_project_user) -> None:
        org, project, user = org_project_user
        resp = client.get(f"/projects/{project.id}")
        assert 'name="approval_status"' not in resp.text
```

- [ ] **Step 2: Update `status_partial.html`**

Add explicit branches for the A5b `ingestion_status` values, ahead of the existing `processing_status`-based branches (research trace: current file has branches at lines 6/20/46/75). Insert new conditions checking `document.ingestion_status` first:

```jinja
{% elif document.ingestion_status == "QUARANTINED" %}
  <div class="status-card status-pending">
    <p>Upload received. Preparing for validation.</p>
  </div>
{% elif document.ingestion_status == "VALIDATING" %}
  <div class="status-card status-pending" hx-get="/projects/{{ project.id }}/status" hx-trigger="every 2s" hx-swap="outerHTML">
    <p>Validating document type.</p>
  </div>
{% elif document.ingestion_status == "SCANNING" %}
  <div class="status-card status-pending" hx-get="/projects/{{ project.id }}/status" hx-trigger="every 2s" hx-swap="outerHTML">
    <p>Awaiting security scan. This document has passed initial validation and is queued for malware scanning (not yet available in this build).</p>
  </div>
{% elif document.ingestion_status == "REJECTED_TYPE" %}
  <div class="status-card status-failed">
    <p>Document rejected: please upload a valid PDF or DOCX file.</p>
    {# re-upload form, mirroring the existing failed-state re-upload form below #}
  </div>
{% elif document.ingestion_status == "LEGACY_UNVERIFIED" %}
  <div class="status-card status-pending">
    <p>This existing document requires security reprocessing before it can be used.</p>
  </div>
{% endif %}
```

Read the actual current file's exact Jinja structure/CSS classes before editing (the research trace paraphrased line numbers; do not paste unverified template syntax over the real file — fetch it fresh) and integrate these branches consistently with the existing `{% if %}/{% elif %}` chain rather than duplicating logic, ensuring the new branches take priority over (i.e. are checked before) the legacy `processing_status` branches so a `QUARANTINED`/`VALIDATING`/`SCANNING`/`REJECTED_TYPE` document never falls through to the old "pending"/"processing"/"completed"/failed logic, which no longer applies to new A5b-created documents (whose legacy `processing_status` is set to the new placeholder value `"pending_security_scan"` from Task 6 — verify this string doesn't accidentally match the old `in ("pending", "processing")` branch's condition and get double-handled; if it would, add an explicit exclusion or reorder branches so the new `ingestion_status` checks always win).

- [ ] **Step 3: Update `detail.html`**

Remove the `<select name="approval_status">` block (research trace lines 307-313) from the knowledge-upload form entirely — the route (Task 7) already ignores the field, but removing it from the UI prevents user confusion about a control that has no effect. Do not add any new hidden field that re-introduces client-settable approval.

Add a badge/label near the workflow stepper (research trace lines 127-133) for `ingestion_status` states that aren't `completed`-equivalent, using the same safe strings as `status_partial.html`, so `detail.html`'s own independent `processing_status`-based gating (lines 80, 183, 206) isn't misleading for a `QUARANTINED`/`SCANNING`/`REJECTED_TYPE` document — at minimum, ensure those three gates (`Compliance Matrix` button, `Compliance Matrix` card, `Export` card) do NOT show as available for a document that hasn't reached legacy `processing_status == "completed"` AND `ingestion_status == COMPLETED` (add the second condition alongside the first at each of the three sites; since no A5b document can currently reach `COMPLETED` at all, this is inert-but-correct now and becomes load-bearing once A5c-A5e exist).

- [ ] **Step 4: Run template tests, then full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/test_a5b_ui_states.py -v`, then the full suite command.
Expected: all pass; fix any existing template-snapshot/detail-page tests broken by the removed `approval_status` selector (update their assertions rather than re-adding the selector).

- [ ] **Step 5: Update documentation**

In `DEPLOYMENT.md` and `RUNBOOK.md`, find and correct any section describing the old direct-to-storage upload flow (search for "upload", "LOCAL_STORAGE_PATH", "processing_status") and add a short, accurate paragraph: every new upload enters quarantine first; the original filename is display metadata only; storage identifiers are application-generated; browser MIME is untrusted; PDF/DOCX candidate types are independently detected; valid candidates stop at `SCANNING` pending a scanner that does not exist yet (A5c); no parser, retrieval, requirement extraction, or LLM action occurs before `COMPLETED`; knowledge documents cannot be approved at upload time. Do not claim files are clean, safe, sanitized, or malware-free — use language like "passed independent candidate-type detection," never "verified safe."

- [ ] **Step 6: Commit**

```bash
git add app/templates/projects/status_partial.html app/templates/projects/detail.html tests/integration/test_a5b_ui_states.py DEPLOYMENT.md RUNBOOK.md
git commit -m "security: add quarantine-aware UI states and update docs"
```

---

### Task 11: Full regression pass, local integration validation, final commit review

**Files:** none created — verification and packaging only.

- [ ] **Step 1: Run the full test suite**

Run the full suite command (Task 1 Step 6 env vars).
Expected: all pass, including every A1-A5a suite named in the spec (`test_a1_session_weaknesses.py`, `test_a2_*`, `test_a3_*`, `test_a4_*`, `test_a5_ingestion_weaknesses.py`, `tests/unit/test_ingestion_state.py`, `tests/unit/test_document_ingestion_metadata.py`, `test_security_hardening.py`, `test_csrf.py`).

- [ ] **Step 2: Run lint/type gates**

Run: `.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check . && .venv/Scripts/python.exe -m mypy app`
Expected: clean.

- [ ] **Step 3: Run `make check`**

Run: `make check`
Expected: PASS.

- [ ] **Step 4: Local Docker integration pass (abbreviated per spec section 16 — full detail deferred to the controller session, not this task-level plan, since it involves generated local secrets/TLS the controller must supervise)**

This step is intentionally executed by the controller session directly (not delegated to a task-implementer subagent), since it involves generating and later destroying local secrets/TLS material and inspecting live container state — flag this back to the controller as the handoff point rather than attempting it as a plan task.

- [ ] **Step 5: Confirm diff scope**

Run: `git diff --stat c8a8b77505c7d2d43c647c921072d5c6bc780f1a...HEAD`
Expected: only files named in this plan's File Structure section changed, plus any existing test files explicitly adapted in Tasks 7/8/10 for the new upload behavior. No ClamAV, EICAR, parser-service, or A5c-scoped files present.

- [ ] **Step 6: Report readiness for controller-driven local integration validation and final PR**

Do not push or open the PR from within this task — report back to the controller session with the full diff-stat summary and test-count summary for the controller to run the local Docker integration pass (spec section 16) and then execute the commit/push/PR steps (spec section 19) itself.

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers spec §3 (test fixture repair). Task 2 covers §4 (quarantine config). Task 3 covers §5-6 (quarantine storage service, storage identifiers, file creation, streaming, filename normalization). Tasks 4-5 cover §7 (PDF/DOCX candidate detection). Task 6 covers §8 (shared ingestion service, state transitions, failure handling). Task 7 covers §8 (route wiring), §9 (knowledge approval gating), §10 (RFP replacement + concurrency). Task 8 covers §11 (legacy pipeline disablement) plus the retrieval/evidence gates implied by the state-machine invariants (not explicitly itemized as its own numbered section in the spec, but required by §9's "retrieval queries must continue excluding incomplete documents" and the general A5a invariant that only `COMPLETED` documents are usable evidence). Task 9 covers §12 (Compose) and §14 (readiness). Task 10 covers §13 (UI) and §18 (docs). Task 11 covers §16-17 (validation) and hands off §19 (commit/PR) to the controller.
- **Placeholder scan:** no TBD/TODO; every step has runnable code. A few steps explicitly instruct the implementer to "read the real file first and adapt" (e.g. exact Jinja structure, exact `retrieve_evidence` signature) rather than guessing — this is intentional given several of these files weren't fully quoted in the research trace, not a placeholder; each such instruction is paired with a concrete code sketch to adapt, not a vague "add appropriate handling."
- **Type consistency:** `IngestionStatus`, `DetectedType`, `DetectionResult`, `QuarantineWriteResult`, `QuarantineStorageError` used identically across all tasks that reference them. `ingest_uploaded_document`'s signature in Task 6 matches its call sites in Task 7. Corrected an internal inconsistency during self-review: the File Structure section's Compose bullet initially implied both `app` and `worker` get the quarantine mount; Task 9 Step 4 explicitly corrects this to `app`-only per spec §12's explicit "worker must not receive the quarantine mount during A5b" requirement — flagging this here so the executing agent does not propagate the earlier, wrong summary.
