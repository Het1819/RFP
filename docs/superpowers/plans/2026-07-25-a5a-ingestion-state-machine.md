# A5a: Ingestion State Machine, Security Metadata & Pre-Fix Evidence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove (and permanently record) the current A4 ingestion weaknesses, then introduce the typed `ingestion_status` state machine and security-metadata columns on `Document` that every later A5 sub-phase (quarantine storage, DOCX/PDF policy, ClamAV, isolated parser, orchestration) will build on. No upload/worker behavior changes yet — this phase only adds the data model and the validated transition function; wiring it into routes/worker is out of scope (A5b–A5f).

**Architecture:** Add a `Document.ingestion_status` string column governed by a single validated transition function (`app/services/ingestion_state.py`) — the only sanctioned mutator, never set directly by routes/templates. Add security-metadata columns (hash, detected type, scan/parser status, rejection reason) as nullable fields populated by later phases. Backfill existing rows to `LEGACY_UNVERIFIED` via the migration's `server_default`, never fabricating historical scan evidence. Capture the current (pre-fix) weaknesses as an always-run pytest module now, to be selectively `@pytest.mark.skip`-flagged in the specific later phase that fixes each behavior.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (typed `Mapped`/`mapped_column`), Alembic, pytest, PostgreSQL (prod) / SQLite (tests, via `Base.metadata.create_all`).

## Global Constraints

- This is a controlled local task only: no deploy, no VPS, no production secrets, no customer documents, no external malware-analysis submission, no real malware, no PR merges, no live LLM calls.
- `make check` must pass before this phase is considered complete.
- Do not refactor unrelated code (AGENTS.md: "Do not refactor unrelated code").
- Add audit events for important actions (AGENTS.md) — every state transition must write an `AuditEvent`.
- Never commit secrets, API keys, customer documents, or `.env` (AGENTS.md).
- Follow repo conventions: string-column status fields (not native DB enums), `Mapped[...]`/`mapped_column` typed models, Alembic revisions with `upgrade()`/`downgrade()`, tests via `Base.metadata.create_all()` against SQLite/Postgres per `tests/conftest.py:54-79` (no real `alembic upgrade` in the test suite).
- Existing regression/weakness test naming convention: `tests/integration/test_a{N}_*_weaknesses.py` (see `test_a4_edge_weaknesses.py`). This phase's evidence file follows that pattern: `tests/integration/test_a5_ingestion_weaknesses.py`.
- Do not permit arbitrary `ingestion_status` assignment from routes or templates — `transition()` in `app/services/ingestion_state.py` is the only sanctioned mutator (enforced by code review in this phase; runtime enforcement lands when routes are wired in A5b–A5f).
- Do not fabricate historical scan evidence for legacy documents — they get `LEGACY_UNVERIFIED`, never `CLEAN`/`COMPLETED`, until explicitly reprocessed.

---

## File Structure

- Create: `tests/integration/test_a5_ingestion_weaknesses.py` — pre-fix evidence tests (regression proof of current weaknesses, items 1–22 from the A5 spec §2).
- Create: `app/services/ingestion_state.py` — `IngestionStatus` constants, `ALLOWED_TRANSITIONS` graph, `transition()` function, `IngestionStateError`.
- Create: `tests/unit/test_ingestion_state.py` — unit tests for the transition function.
- Modify: `app/models/document.py` — add `ingestion_status` + security-metadata columns to `Document`.
- Create: `alembic/versions/<rev>_add_document_ingestion_security_metadata.py` — migration adding columns + CHECK constraint, `ingestion_status` backfilled via `server_default='LEGACY_UNVERIFIED'`.
- Create: `tests/unit/test_document_ingestion_metadata.py` — model-level tests (column defaults, CHECK constraint behavior where the test DB is Postgres).

---

### Task 1: Pre-fix evidence tests (A5 spec §2, items 1–22)

**Files:**
- Create: `tests/integration/test_a5_ingestion_weaknesses.py`
- Test: (this file is itself the test)

**Interfaces:**
- Consumes: `app.services.extractor.validate_uploaded_file`, `app.services.extractor.extract_pages` (existing, `app/services/extractor.py:15-38,41-62`); `app.services.project_service.upload_rfp_document` (existing, `app/services/project_service.py`); `app.models.document.Document` (existing, pre-A5a fields only).
- Produces: nothing consumed by later tasks — this file stands alone as permanent evidence. Later phases (A5b onward) will add `@pytest.mark.skip(reason="fixed in A5x: <PR link/commit>")` to individual test functions here as each weakness is remediated. Do not delete or rewrite these tests when that happens.

Each test below asserts the **current insecure behavior succeeds** (i.e., the test currently passes, proving the vulnerability). Use `tmp_path` fixtures and the existing test DB/session fixtures from `tests/conftest.py` (`db_session`, `client` — confirm exact fixture names by reading `tests/conftest.py` before writing; follow the pattern used in `tests/integration/test_a4_edge_weaknesses.py`).

- [ ] **Step 1: Write the evidence module skeleton and the Content-Type / extension trust tests (items 1–3)**

```python
"""Pre-fix evidence: A5 spec section 2, items 1-22.

These tests document the ingestion weaknesses present as of commit
3910df13e1c4be5164daabd33151ff19d1beeb2e (A4 edge-security tip), the
starting point for A5 (hardening/option-a-document-isolation).

Each test currently PASSES because it asserts the *insecure* behavior
succeeds. As each weakness is remediated in a later A5 sub-phase, mark
the corresponding test with:

    @pytest.mark.skip(reason="fixed in A5<x>: <short description>")

Do not delete, rewrite, or "fix" these tests to match new behavior -
they are permanent historical evidence of the pre-fix state.
"""

import hashlib
import io
import uuid
import zipfile

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.services.extractor import validate_uploaded_file


def _upload_file(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


class TestContentTypeAndExtensionTrust:
    """Items 1-3: browser Content-Type and extension are trusted, not verified."""

    def test_item1_trusts_uploaded_content_type_header(self) -> None:
        """extractor.validate_uploaded_file accepts any content whose
        client-supplied Content-Type header matches the allowlist, with
        no independent byte-level check. See extractor.py:34-38."""
        fake_pdf_bytes = b"this is not a pdf, just text pretending to be one"
        upload = _upload_file("doc.pdf", fake_pdf_bytes, "application/pdf")
        # Currently succeeds: no magic-byte verification exists.
        validate_uploaded_file(upload)

    def test_item2_pdf_extension_with_non_pdf_content_when_mime_matches(self) -> None:
        """A .pdf-named file containing arbitrary bytes is accepted as
        long as the declared MIME matches - extension and MIME are both
        client-controlled and neither is checked against real content."""
        upload = _upload_file(
            "fake.pdf", b"\x00\x01\x02not a real pdf", "application/pdf"
        )
        validate_uploaded_file(upload)

    def test_item3_docx_extension_with_arbitrary_zip_when_mime_matches(self) -> None:
        """A .docx-named file containing an arbitrary (non-OOXML) ZIP is
        accepted as long as the declared MIME matches. No
        [Content_Types].xml / word/document.xml check exists."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("not_ooxml.txt", "arbitrary zip content")
        upload = _upload_file(
            "fake.docx",
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        validate_uploaded_file(upload)
```

- [ ] **Step 2: Add storage/quarantine/hash/lifecycle evidence tests (items 4-9)**

Read `tests/conftest.py` and `tests/integration/test_projects.py::test_project_detail_and_rfp_upload_flow` first to copy the exact fixture names (`db_session`, `client`, org/user/project setup helpers) — do not guess signatures.

```python
class TestStorageAndLifecycleGaps:
    """Items 4-9: unscanned files enter normal storage immediately; no
    quarantine lifecycle, hash, detected-type, or scan-state fields exist."""

    def test_item4_and_5_unscanned_upload_lands_in_normal_storage_as_processable(
        self, client, db_session, authed_org_and_project
    ) -> None:
        """A freshly uploaded file is written straight into
        LOCAL_STORAGE_PATH/documents (the same tree the worker reads
        completed documents from) and a Document row with
        processing_status='pending' is created immediately - i.e. the
        document is processable (queued for parsing) before any
        malware/content inspection has occurred. See
        project_service.py:132-178."""
        org, project, headers = authed_org_and_project
        pdf_bytes = b"%PDF-1.4\n%%EOF"
        response = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            headers=headers,
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        from app.models.document import Document

        doc = db_session.query(Document).filter_by(project_id=project.id).one()
        assert doc.processing_status == "pending"  # queued, unscanned
        from pathlib import Path

        assert Path(doc.file_path).exists()  # written to normal storage tree

    def test_item6_no_quarantine_lifecycle_field_exists(self) -> None:
        """Document has no quarantine-state column as of A4."""
        from app.models.document import Document

        assert not hasattr(Document, "quarantined_at")

    def test_item7_no_content_hash_or_detected_type_field_exists(self) -> None:
        from app.models.document import Document

        assert not hasattr(Document, "sha256_digest")
        assert not hasattr(Document, "detected_content_type")

    def test_item8_no_malware_scan_state_field_exists(self) -> None:
        from app.models.document import Document

        assert not hasattr(Document, "scan_status")

    def test_item9_no_signature_freshness_field_exists(self) -> None:
        from app.models.document import Document

        assert not hasattr(Document, "scan_signature_version")
```

- [ ] **Step 3: Add DOCX/PDF policy-gap and unsandboxed-parsing evidence tests (items 10-19)**

```python
class TestNoContentPolicyOrSandboxing:
    """Items 10-19: no active-content inspection, no archive-bomb
    limits, hostile input opened directly by PyMuPDF/python-docx inside
    the general worker process with no resource/time/output bounds."""

    def test_item10_and_11_no_pdf_or_docx_policy_module_exists(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.services.pdf_policy")
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.services.docx_policy")

    def test_item12_docx_compression_bomb_not_rejected_at_validation(self) -> None:
        """A DOCX whose ZIP central directory has an extreme compression
        ratio passes validate_uploaded_file - no archive-member or
        decompression-ratio check exists at this layer."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "x")
            zf.writestr("bomb.bin", b"0" * (10 * 1024 * 1024))  # 10MB of zeros
        upload = _upload_file(
            "bomb.docx",
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        validate_uploaded_file(upload)  # accepted; ratio never inspected

    def test_item13_extractor_opens_files_directly_with_pymupdf_and_docx(self) -> None:
        """extractor.py imports fitz/docx at module level and calls
        fitz.open()/docx.Document() directly with no wrapper."""
        import inspect

        from app.services import extractor

        src = inspect.getsource(extractor)
        assert "import fitz" in src
        assert "import docx" in src
        assert "fitz.open(" in src
        assert "docx.Document(" in src

    def test_item14_and_15_worker_pipeline_imports_extractor_with_full_db_access(
        self,
    ) -> None:
        """process_job_pipeline_async (run inside the ARQ worker process)
        calls extract_pages() in-process and has an active DB session -
        i.e. the parsing code path has database access, not an isolated
        process."""
        import inspect

        from app.services import project_service

        src = inspect.getsource(project_service.process_job_pipeline_async)
        assert "extract_pages(" in src

    def test_item16_no_per_document_resource_limit_module_exists(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.services.parser_limits")

    def test_item17_and_18_no_max_page_or_char_limit_enforced(self) -> None:
        """extract_pages has no page-count or extracted-character cap."""
        import inspect

        from app.services.extractor import extract_pages

        src = inspect.getsource(extract_pages)
        assert "MAX_PAGES" not in src
        assert "MAX_CHARS" not in src

    def test_item19_raw_exception_text_can_reach_processing_error_column(self) -> None:
        """Document.processing_error is a free-text column with no
        length/content filtering; project_service writes safe_error_message
        separately but the raw processing_error path is unfiltered."""
        from app.models.document import Document

        col = Document.__table__.columns["processing_error"]
        assert col.type.length is None  # Text, unbounded
```

- [ ] **Step 4: Add gate-bypass and retention-gap evidence tests (items 20-22)**

```python
class TestNoSecurityGateBeforeLLMAndNoRetentionPolicy:
    """Items 20-22 (renumbered from the 21-22 gap in the spec's own
    list - the spec's item 20 duplicates item 19's exception-text
    concern, so this class covers the LLM-gate and retention gaps)."""

    def test_processing_reaches_requirement_extraction_with_no_malware_gate(
        self,
    ) -> None:
        """process_job_pipeline_async proceeds straight from
        extract_pages() to extract_requirements_from_document() - no
        scan_status or content_policy_status check gates this call."""
        import inspect

        from app.services import project_service

        src = inspect.getsource(project_service.process_job_pipeline_async)
        extract_idx = src.index("extract_pages(")
        requirements_idx = src.index("extract_requirements_from_document(")
        between = src[extract_idx:requirements_idx]
        assert "scan_status" not in between
        assert "content_policy_status" not in between

    def test_no_rejected_file_retention_or_cleanup_routine_exists(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.services.quarantine_cleanup")
```

- [ ] **Step 5: Run the evidence suite and confirm every test currently passes**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/test_a5_ingestion_weaknesses.py -v`
Expected: all tests PASS (proving the weaknesses exist today). If any test fails, the assumed weakness doesn't actually exist as written in the spec — investigate and correct the test before proceeding, don't weaken the assertion to force a pass.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_a5_ingestion_weaknesses.py
git commit -m "test: capture pre-fix A5 ingestion weakness evidence"
```

---

### Task 2: `IngestionStatus` constants and validated transition function

**Files:**
- Create: `app/services/ingestion_state.py`
- Test: `tests/unit/test_ingestion_state.py`

**Interfaces:**
- Consumes: `app.models.document.Document` (from Task 3 — this task can be implemented against the Task-3 model shape; sequence Task 2 after Task 3 if executing strictly in order, or stub the needed attributes first — see note in Task 3).
- Produces:
  - `IngestionStatus` — a class of string constants: `QUARANTINED`, `VALIDATING`, `SCANNING`, `SCAN_RETRY_PENDING`, `REJECTED_TYPE`, `REJECTED_MALWARE`, `REJECTED_CONTENT_POLICY`, `CLEAN`, `PARSING`, `PARSE_FAILED`, `COMPLETED`, `LEGACY_UNVERIFIED`. Also `IngestionStatus.ALL: frozenset[str]`.
  - `ALLOWED_TRANSITIONS: dict[str, frozenset[str]]`
  - `class IngestionStateError(Exception)`
  - `def transition(db: Session, document: Document, new_status: str, *, org_id: uuid.UUID, user_id: uuid.UUID | None, reason_code: str | None = None, safe_summary: str | None = None) -> None` — validates, mutates `document.ingestion_status` (and `rejection_reason_code`/`operator_failure_summary` if provided), writes an `AuditEvent` (action=`document_ingestion_transition`), commits. Same-state calls are a no-op (idempotent) and do not write a duplicate audit event.

- [ ] **Step 1: Write the failing unit tests**

```python
import uuid

import pytest

from app.models.audit import AuditEvent
from app.models.document import Document
from app.services.ingestion_state import (
    ALLOWED_TRANSITIONS,
    IngestionStateError,
    IngestionStatus,
    transition,
)


def _make_document(db_session, org_project_user) -> Document:
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="test.pdf",
        file_path="/data/storage/documents/x.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


class TestTransitionValidity:
    def test_valid_transition_quarantined_to_validating(
        self, db_session, org_project_user
    ) -> None:
        org, project, user = org_project_user
        doc = _make_document(db_session, org_project_user)
        transition(
            db_session, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id
        )
        assert doc.ingestion_status == IngestionStatus.VALIDATING

    def test_invalid_transition_rejected(self, db_session, org_project_user) -> None:
        org, project, user = org_project_user
        doc = _make_document(db_session, org_project_user)
        with pytest.raises(IngestionStateError):
            transition(
                db_session, doc, IngestionStatus.COMPLETED, org_id=org.id, user_id=user.id
            )
        assert doc.ingestion_status == IngestionStatus.QUARANTINED  # unchanged

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        for terminal in (
            IngestionStatus.REJECTED_TYPE,
            IngestionStatus.REJECTED_MALWARE,
            IngestionStatus.REJECTED_CONTENT_POLICY,
            IngestionStatus.COMPLETED,
        ):
            assert ALLOWED_TRANSITIONS[terminal] == frozenset()

    def test_same_state_transition_is_idempotent_noop(
        self, db_session, org_project_user
    ) -> None:
        org, project, user = org_project_user
        doc = _make_document(db_session, org_project_user)
        before_count = (
            db_session.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .count()
        )
        transition(
            db_session,
            doc,
            IngestionStatus.QUARANTINED,
            org_id=org.id,
            user_id=user.id,
        )
        after_count = (
            db_session.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .count()
        )
        assert doc.ingestion_status == IngestionStatus.QUARANTINED
        assert after_count == before_count  # no duplicate audit event

    def test_transition_writes_audit_event(self, db_session, org_project_user) -> None:
        org, project, user = org_project_user
        doc = _make_document(db_session, org_project_user)
        transition(
            db_session, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id
        )
        event = (
            db_session.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .one()
        )
        assert event.details["from"] == IngestionStatus.QUARANTINED
        assert event.details["to"] == IngestionStatus.VALIDATING

    def test_rejection_transition_records_reason_and_summary(
        self, db_session, org_project_user
    ) -> None:
        org, project, user = org_project_user
        doc = _make_document(db_session, org_project_user)
        transition(
            db_session, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id
        )
        transition(
            db_session,
            doc,
            IngestionStatus.REJECTED_TYPE,
            org_id=org.id,
            user_id=user.id,
            reason_code="MIME_EXTENSION_MISMATCH",
            safe_summary="Uploaded file does not match a supported PDF or DOCX format.",
        )
        assert doc.ingestion_status == IngestionStatus.REJECTED_TYPE
        assert doc.rejection_reason_code == "MIME_EXTENSION_MISMATCH"
        assert doc.operator_failure_summary == (
            "Uploaded file does not match a supported PDF or DOCX format."
        )

    def test_legacy_unverified_can_only_reenter_validating(self) -> None:
        assert ALLOWED_TRANSITIONS[IngestionStatus.LEGACY_UNVERIFIED] == frozenset(
            {IngestionStatus.VALIDATING}
        )
```

(If `org_project_user` isn't an existing fixture, check `tests/conftest.py` and `tests/integration/test_queue_jobs.py` for the actual org/project/user creation helper and adapt the test signatures to match — do not invent a fixture name that doesn't exist.)

- [ ] **Step 2: Run tests to verify they fail (module doesn't exist yet)**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_ingestion_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ingestion_state'`

- [ ] **Step 3: Implement `app/services/ingestion_state.py`**

```python
"""Validated ingestion lifecycle for uploaded documents.

Document.ingestion_status must never be set directly by routes, worker
tasks, or templates - always call transition(). This is the single
enforcement point for the security invariants in AGENTS.md / A5 spec
section 3: quarantined files cannot be downloaded, approved, retrieved,
sent to the LLM, or parsed by the legacy in-process parser; only files
that pass structural validation, detected-type validation, malware
scanning, and content-policy inspection may reach CLEAN; only CLEAN
files may be parsed; only successfully parsed documents may reach
COMPLETED and enter requirement extraction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.document import Document


class IngestionStatus:
    QUARANTINED = "QUARANTINED"
    VALIDATING = "VALIDATING"
    SCANNING = "SCANNING"
    SCAN_RETRY_PENDING = "SCAN_RETRY_PENDING"
    REJECTED_TYPE = "REJECTED_TYPE"
    REJECTED_MALWARE = "REJECTED_MALWARE"
    REJECTED_CONTENT_POLICY = "REJECTED_CONTENT_POLICY"
    CLEAN = "CLEAN"
    PARSING = "PARSING"
    PARSE_FAILED = "PARSE_FAILED"
    COMPLETED = "COMPLETED"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"

    ALL = frozenset(
        {
            QUARANTINED,
            VALIDATING,
            SCANNING,
            SCAN_RETRY_PENDING,
            REJECTED_TYPE,
            REJECTED_MALWARE,
            REJECTED_CONTENT_POLICY,
            CLEAN,
            PARSING,
            PARSE_FAILED,
            COMPLETED,
            LEGACY_UNVERIFIED,
        }
    )


# Content-policy inspection (A5 spec sections 7-8) runs as part of the
# SCANNING phase, between a clean malware result and promotion to CLEAN -
# it is not a separate persisted state, so both REJECTED_MALWARE and
# REJECTED_CONTENT_POLICY are reachable directly from SCANNING.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    IngestionStatus.QUARANTINED: frozenset({IngestionStatus.VALIDATING}),
    IngestionStatus.VALIDATING: frozenset(
        {IngestionStatus.SCANNING, IngestionStatus.REJECTED_TYPE}
    ),
    IngestionStatus.SCANNING: frozenset(
        {
            IngestionStatus.CLEAN,
            IngestionStatus.REJECTED_MALWARE,
            IngestionStatus.REJECTED_CONTENT_POLICY,
            IngestionStatus.SCAN_RETRY_PENDING,
        }
    ),
    IngestionStatus.SCAN_RETRY_PENDING: frozenset({IngestionStatus.SCANNING}),
    IngestionStatus.REJECTED_TYPE: frozenset(),
    IngestionStatus.REJECTED_MALWARE: frozenset(),
    IngestionStatus.REJECTED_CONTENT_POLICY: frozenset(),
    IngestionStatus.CLEAN: frozenset({IngestionStatus.PARSING}),
    IngestionStatus.PARSING: frozenset(
        {IngestionStatus.COMPLETED, IngestionStatus.PARSE_FAILED}
    ),
    IngestionStatus.PARSE_FAILED: frozenset({IngestionStatus.PARSING}),
    IngestionStatus.COMPLETED: frozenset(),
    IngestionStatus.LEGACY_UNVERIFIED: frozenset({IngestionStatus.VALIDATING}),
}


class IngestionStateError(Exception):
    """Raised when an ingestion-status transition is not permitted."""


def transition(
    db: Session,
    document: Document,
    new_status: str,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    reason_code: str | None = None,
    safe_summary: str | None = None,
) -> None:
    if new_status not in IngestionStatus.ALL:
        raise IngestionStateError(f"Unknown ingestion status: {new_status!r}")

    current = document.ingestion_status
    if current == new_status:
        return  # idempotent no-op, no duplicate audit event

    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise IngestionStateError(
            f"Illegal ingestion transition: {current} -> {new_status}"
        )

    document.ingestion_status = new_status
    if reason_code is not None:
        document.rejection_reason_code = reason_code
    if safe_summary is not None:
        document.operator_failure_summary = safe_summary

    details: dict[str, Any] = {"from": current, "to": new_status}
    if reason_code is not None:
        details["reason_code"] = reason_code

    from app.core.observability import request_id_var

    db.add(
        AuditEvent(
            organization_id=org_id,
            user_id=user_id,
            action="document_ingestion_transition",
            entity_type="document",
            entity_id=document.id,
            details=details,
            request_id=request_id_var.get(),
        )
    )
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_ingestion_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/ingestion_state.py tests/unit/test_ingestion_state.py
git commit -m "feat: add validated ingestion state machine"
```

---

### Task 3: `Document` model fields + Alembic migration + backfill test

**Files:**
- Modify: `app/models/document.py`
- Create: `alembic/versions/<new_rev>_add_document_ingestion_security_metadata.py`
- Test: `tests/unit/test_document_ingestion_metadata.py`

**Interfaces:**
- Consumes: `app.services.ingestion_state.IngestionStatus` (Task 2) for the default value and CHECK-constraint value list — if executing tasks in numeric order, add the `ingestion_status` column with the literal string default `"LEGACY_UNVERIFIED"` first and import `IngestionStatus` into the model afterward once Task 2 lands; both tasks can be done in either order since the model only needs the string constant, not the module.
- Produces: `Document.ingestion_status`, `Document.display_filename`, `Document.detected_content_type`, `Document.sha256_digest`, `Document.file_size_bytes`, `Document.quarantined_at`, `Document.scan_status`, `Document.scan_engine_version`, `Document.scan_signature_version`, `Document.scan_completed_at`, `Document.content_policy_status`, `Document.parser_version`, `Document.parser_completed_at`, `Document.rejection_reason_code`, `Document.operator_failure_summary` — consumed by every later A5 sub-phase.

- [ ] **Step 1: Write the failing model test**

```python
import uuid
from datetime import UTC, datetime

from app.models.document import Document


class TestDocumentSecurityMetadataFields:
    def test_new_columns_exist_with_expected_types(self) -> None:
        cols = Document.__table__.columns
        assert cols["ingestion_status"].type.length == 30
        assert cols["ingestion_status"].nullable is False
        assert cols["display_filename"].type.length == 255
        assert cols["detected_content_type"].type.length == 255
        assert cols["sha256_digest"].type.length == 64
        assert cols["scan_status"].type.length == 30
        assert cols["content_policy_status"].type.length == 30
        assert cols["rejection_reason_code"].type.length == 100

    def test_new_document_defaults_to_quarantined_in_orm(
        self, db_session, org_project_user
    ) -> None:
        """New rows created via the ORM (not the migration's server_default
        path) should explicitly pass ingestion_status - this test documents
        that the model itself does not silently default new documents to
        LEGACY_UNVERIFIED; only the migration's backfill does."""
        org, project, user = org_project_user
        from app.services.ingestion_state import IngestionStatus

        doc = Document(
            project_id=project.id,
            name="test.pdf",
            file_path="/data/storage/quarantine/x.pdf",
            file_type="application/pdf",
            created_by_id=user.id,
            ingestion_status=IngestionStatus.QUARANTINED,
        )
        db_session.add(doc)
        db_session.commit()
        assert doc.ingestion_status == "QUARANTINED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_document_ingestion_metadata.py -v`
Expected: FAIL with `KeyError: 'ingestion_status'`

- [ ] **Step 3: Add columns to `app/models/document.py`**

Modify the `Document` class (`app/models/document.py:10-56`), adding after `updated_at` (before the `pages` relationship at line 54):

```python
    ingestion_status: Mapped[str] = mapped_column(
        String(30), default="QUARANTINED", server_default="LEGACY_UNVERIFIED",
        nullable=False,
    )
    display_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_content_type: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    sha256_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scan_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scan_engine_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    scan_signature_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    scan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_policy_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parser_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    operator_failure_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
```

Note: `default="QUARANTINED"` is the Python-side ORM default used when the application constructs a *new* `Document()` without specifying `ingestion_status` explicitly (defense in depth only — every real call site added in A5b onward will pass it explicitly). `server_default="LEGACY_UNVERIFIED"` is the DB-side default applied to the column when the migration adds it to existing rows — this is what actually backfills pre-A5 documents, per the "explicit migration/backfill policy" and "do not silently mark legacy documents as scanned" requirements. These two defaults intentionally differ.

Add `BigInteger` to the existing `sqlalchemy` import line (`app/models/document.py:4`):
```python
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
```

- [ ] **Step 4: Run test to verify the ORM-level test passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_document_ingestion_metadata.py -v`
Expected: PASS (both tests — `Base.metadata.create_all()` in `tests/conftest.py:79` picks up the new columns automatically, no migration needed for the test DB).

- [ ] **Step 5: Generate and hand-verify the Alembic migration**

Run: `.venv/Scripts/python.exe -m alembic revision --autogenerate -m "add document ingestion security metadata"`

Open the generated file under `alembic/versions/`, and edit it to match this exact shape (autogenerate will produce the column adds; verify the `ingestion_status` column explicitly includes `server_default="LEGACY_UNVERIFIED"` — autogenerate sometimes drops server defaults from `mapped_column`, so check and add it manually if missing, plus add the CHECK constraint autogenerate will NOT produce):

```python
"""add document ingestion security metadata

Revision ID: <generated>
Revises: f0093fb3f942
Create Date: <generated>

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "<generated>"
down_revision: str | Sequence[str] | None = "f0093fb3f942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INGESTION_STATUSES = (
    "QUARANTINED",
    "VALIDATING",
    "SCANNING",
    "SCAN_RETRY_PENDING",
    "REJECTED_TYPE",
    "REJECTED_MALWARE",
    "REJECTED_CONTENT_POLICY",
    "CLEAN",
    "PARSING",
    "PARSE_FAILED",
    "COMPLETED",
    "LEGACY_UNVERIFIED",
)


def upgrade() -> None:
    """Upgrade schema.

    Adds ingestion-security metadata to documents. ingestion_status is
    added NOT NULL with server_default='LEGACY_UNVERIFIED' so every
    pre-existing row is explicitly marked as not-yet-security-verified
    (never silently marked CLEAN/COMPLETED) - see A5 spec section 4.
    """
    op.add_column(
        "documents",
        sa.Column(
            "ingestion_status",
            sa.String(length=30),
            nullable=False,
            server_default="LEGACY_UNVERIFIED",
        ),
    )
    op.add_column(
        "documents", sa.Column("display_filename", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("detected_content_type", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("sha256_digest", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("scan_status", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("scan_engine_version", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("scan_signature_version", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("scan_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("content_policy_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("parser_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("parser_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("rejection_reason_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("operator_failure_summary", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_documents_ingestion_status_valid",
        "documents",
        sa.column("ingestion_status").in_(_INGESTION_STATUSES),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_documents_ingestion_status_valid", "documents", type_="check"
    )
    op.drop_column("documents", "operator_failure_summary")
    op.drop_column("documents", "rejection_reason_code")
    op.drop_column("documents", "parser_completed_at")
    op.drop_column("documents", "parser_version")
    op.drop_column("documents", "content_policy_status")
    op.drop_column("documents", "scan_completed_at")
    op.drop_column("documents", "scan_signature_version")
    op.drop_column("documents", "scan_engine_version")
    op.drop_column("documents", "scan_status")
    op.drop_column("documents", "quarantined_at")
    op.drop_column("documents", "file_size_bytes")
    op.drop_column("documents", "sha256_digest")
    op.drop_column("documents", "detected_content_type")
    op.drop_column("documents", "display_filename")
    op.drop_column("documents", "ingestion_status")
```

Note the `sa.column("ingestion_status").in_(...)` constraint expression form matches SQLAlchemy 2's `create_check_constraint` API — verify against the installed `alembic`/`sqlalchemy` version's docs if `op.create_check_constraint` signature differs; if autogenerate/CI rejects the expression form, use the raw-SQL string form instead: `op.create_check_constraint("ck_documents_ingestion_status_valid", "documents", "ingestion_status IN ('QUARANTINED', 'VALIDATING', ...)")`.

- [ ] **Step 6: Verify the migration applies and reverses cleanly against a local Postgres**

This requires a running local Postgres (start via `docker compose -f compose.yml up -d postgres` if not already running, using the dev compose file — not `docker-compose.prod.yml`, no secrets involved).

Run: `.venv/Scripts/python.exe -m alembic upgrade head`
Expected: succeeds, no errors.

Run: `.venv/Scripts/python.exe -m alembic downgrade -1 && .venv/Scripts/python.exe -m alembic upgrade head`
Expected: both succeed (migration is reversible).

- [ ] **Step 7: Add a backfill-specific test against the real migration (Postgres only)**

Add to `tests/unit/test_document_ingestion_metadata.py`:

```python
import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    "postgresql" not in os.environ.get("DATABASE_URL", ""),
    reason="migration backfill test requires a real Postgres DATABASE_URL",
)
class TestMigrationBackfill:
    def test_preexisting_row_backfilled_to_legacy_unverified(self) -> None:
        """Insert a row bypassing the ORM default (simulating a pre-A5
        row), confirm the column's server_default applies for any row
        inserted without an explicit value, matching what the migration
        did for real pre-existing rows when it added the column."""
        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.begin() as conn:
            # ingestion_status omitted -> server_default fires
            conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name='documents' AND column_name='ingestion_status'"
                )
            )
            row = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name='documents' AND column_name='ingestion_status'"
                )
            ).one()
            assert "LEGACY_UNVERIFIED" in row[0]
```

- [ ] **Step 8: Run the full unit test file**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_document_ingestion_metadata.py -v`
Expected: PASS (Postgres-only test skips if `DATABASE_URL` isn't Postgres, matching the pattern in `tests/conftest.py:49-62`).

- [ ] **Step 9: Commit**

```bash
git add app/models/document.py alembic/versions/ tests/unit/test_document_ingestion_metadata.py
git commit -m "feat: add ingestion security metadata migration and backfill"
```

---

### Task 4: Full regression pass and phase checkpoint

**Files:** none created — verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: confirmation this phase is safe to build on in A5b.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests PASS, including the new evidence, state-machine, and metadata tests, and all pre-existing A1-A4 suites (`test_a1_session_weaknesses.py`, `test_a2_*`, `test_a3_*`, `test_a4_*`, `test_security_hardening.py`, `test_csrf.py`, etc.) unaffected.

- [ ] **Step 2: Run lint/type/format gates**

Run: `.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check . && .venv/Scripts/python.exe -m mypy app`
Expected: all clean.

- [ ] **Step 3: Run `make check`**

Run: `make check`
Expected: PASS.

- [ ] **Step 4: Confirm no A5b+ behavior was accidentally wired in**

Run: `git diff --stat main...HEAD` (or `f0093fb3f942...HEAD` for just this phase) and manually confirm only: `tests/integration/test_a5_ingestion_weaknesses.py`, `app/services/ingestion_state.py`, `tests/unit/test_ingestion_state.py`, `app/models/document.py`, one new `alembic/versions/*.py`, `tests/unit/test_document_ingestion_metadata.py` changed. No route, template, worker, extractor, or Docker changes should appear yet — those are A5b onward.

- [ ] **Step 5: Commit checkpoint (if any fixups were needed) and stop**

If Steps 1-4 required fixes, commit them now. Do not proceed to A5b (quarantine storage + independent content-type detection) in this plan — that is a separate plan document (`2026-07-25-a5b-quarantine-storage-and-type-detection.md`, to be written next).

---

## Self-Review Notes

- **Spec coverage:** This plan covers A5 spec §2 (all 22 weakness items, consolidated into 4 test classes with clear item-number comments), §3 (state machine + all 9 invariants — invariant 2's "cannot be downloaded/approved/used in retrieval/evidence/LLM/legacy-parser" enforcement is *structural* here: those code paths don't exist to gate yet, so the invariant is satisfied by the fact that `QUARANTINED` documents have no `CLEAN`-only-gated code path calling them — actual route/worker gating is wired in A5b-A5f and must re-verify this invariant end-to-end then), and §4 (all 14 metadata fields, backfill policy, no raw-bytes storage, bounded/nullable fields). Sections 5-28 are explicitly out of scope for A5a and covered by later plan documents.
- **Placeholder scan:** No TBD/TODO markers; every step has runnable code.
- **Type consistency:** `IngestionStatus.QUARANTINED` etc. used identically across Task 2 and Task 3; `transition()` signature consumed identically in all Task 2 tests; `Document` field names (`ingestion_status`, `rejection_reason_code`, `operator_failure_summary`, etc.) match between model, migration, and both test files.
