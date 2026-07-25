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

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile
from reportlab.pdfgen import canvas
from sqlalchemy import select
from starlette.datastructures import Headers

from app.core.config import settings
from app.core.database import get_default_org_and_user
from app.models.document import Document
from app.models.project import ProposalProject
from app.services.extractor import validate_uploaded_file


def _make_valid_pdf(text: str = "Requirement 1: system must be secure.") -> bytes:
    """Build a minimal but well-formed PDF so the extraction pipeline runs
    to completion. The weakness under test (items 4-5) is about the
    absence of a scan/quarantine gate before storage and processing, not
    about triggering the extractor's own error-handling path, so a valid
    document is the correct fixture here."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


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
        validate_uploaded_file(upload, settings.MAX_UPLOAD_SIZE)

    def test_item2_pdf_extension_with_non_pdf_content_when_mime_matches(self) -> None:
        """A .pdf-named file containing arbitrary bytes is accepted as
        long as the declared MIME matches - extension and MIME are both
        client-controlled and neither is checked against real content."""
        upload = _upload_file(
            "fake.pdf", b"\x00\x01\x02not a real pdf", "application/pdf"
        )
        validate_uploaded_file(upload, settings.MAX_UPLOAD_SIZE)

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
        validate_uploaded_file(upload, settings.MAX_UPLOAD_SIZE)


class TestStorageAndLifecycleGaps:
    """Items 4-9: unscanned files enter normal storage immediately; no
    quarantine lifecycle, hash, detected-type, or scan-state fields exist."""

    def test_item4_and_5_unscanned_upload_lands_in_normal_storage_as_processable(
        self, client, db
    ) -> None:
        """A freshly uploaded file is written straight into
        LOCAL_STORAGE_PATH/documents (the same tree the worker reads
        completed documents from) and a Document row with
        processing_status='pending'-or-later is created immediately -
        i.e. the document is processable (queued for parsing) before any
        malware/content inspection has occurred. See
        project_service.py:132-178.

        Note: FastAPI's TestClient runs BackgroundTasks synchronously, so
        by the time this request returns, the queued job has already run
        to completion (processing_status='completed'). The weakness under
        test is that the file was written to normal (non-quarantine)
        storage and queued for processing immediately upon upload, with
        no scan/quarantine gate in between - not the specific status
        value observed after the synchronous worker finishes.
        """
        org_id, user_id = get_default_org_and_user(db)
        project = ProposalProject(
            organization_id=org_id,
            created_by_id=user_id,
            name="A5 Evidence Project",
            client_name="Acme Corp",
            status="draft",
        )
        db.add(project)
        db.commit()

        pdf_bytes = _make_valid_pdf()
        response = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            follow_redirects=False,
        )
        assert response.status_code == 303

        doc = db.scalars(
            select(Document).where(Document.project_id == project.id)
        ).one()
        # No quarantine gate exists: processing_status is never "quarantined"
        # or "pending_scan" - it proceeds through the normal pipeline states.
        assert doc.processing_status in ("pending", "processing", "completed")
        # Written straight into the normal (non-quarantine) storage tree.
        assert Path(doc.file_path).exists()
        assert "documents" in Path(doc.file_path).parts
        assert "quarantine" not in str(doc.file_path).lower()

    @pytest.mark.skip(
        reason="assertion superseded in A5a task 3 (Document.quarantined_at "
        "schema placeholder column added); behavioral remediation (actual "
        "quarantine lifecycle) lands in A5b"
    )
    def test_item6_no_quarantine_lifecycle_field_exists(self) -> None:
        """Document has no quarantine-state column as of A4."""
        assert not hasattr(Document, "quarantined_at")

    @pytest.mark.skip(
        reason=(
            "assertion superseded in A5a task 3 (Document.sha256_digest and "
            "detected_content_type schema placeholder columns added); "
            "behavioral remediation (actual hash computation and "
            "detected-type checking) lands in A5b"
        )
    )
    def test_item7_no_content_hash_or_detected_type_field_exists(self) -> None:
        assert not hasattr(Document, "sha256_digest")
        assert not hasattr(Document, "detected_content_type")

    @pytest.mark.skip(
        reason="assertion superseded in A5a task 3 (Document.scan_status "
        "schema placeholder column added); behavioral remediation (actual "
        "malware scanning) lands in A5d"
    )
    def test_item8_no_malware_scan_state_field_exists(self) -> None:
        assert not hasattr(Document, "scan_status")

    @pytest.mark.skip(
        reason=(
            "assertion superseded in A5a task 3 (Document.scan_signature_version "
            "schema placeholder column added); behavioral remediation (actual "
            "signature-freshness tracking) lands in A5d"
        )
    )
    def test_item9_no_signature_freshness_field_exists(self) -> None:
        assert not hasattr(Document, "scan_signature_version")


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
        validate_uploaded_file(upload, settings.MAX_UPLOAD_SIZE)  # accepted

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
        # The same function receives the live DB session as a parameter and
        # uses it throughout (e.g. `db.commit()`), confirming the parsing
        # code path runs with an active DB session, not an isolated process.
        assert (
            "db"
            in inspect.signature(project_service.process_job_pipeline_async).parameters
        )
        assert "db.commit()" in src

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
        length/content filtering; project_service writes a sanitized
        safe_error_message on the ProcessingJob separately, but the raw
        processing_error column itself is unbounded Text with no
        length/content filtering applied by the schema."""
        col = Document.__table__.columns["processing_error"]
        assert col.type.length is None  # Text, unbounded


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
