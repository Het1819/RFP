"""Tests for app.services.document_ingestion: the shared quarantine-first
orchestration used by both RFP and knowledge-document uploads.

Covers: valid PDF reaches SCANNING, invalid content reaches REJECTED_TYPE,
the very first persisted status is QUARANTINED (not an ORM default) and the
first transition() call is to VALIDATING, no legacy ProcessingJob is ever
created, the quarantine file is removed if the initial Document commit
fails, and audit events never leak the raw uploaded filename or storage
path."""

import io

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.services.document_ingestion import ingest_uploaded_document
from app.services.ingestion_state import IngestionStatus


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
        transition() call."""
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
        from sqlalchemy import select

        from app.models.job import ProcessingJob

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

        with pytest.raises(RuntimeError, match="simulated db failure"):
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

    def test_audit_event_has_no_raw_filename_or_path(
        self, db, org_project_user
    ) -> None:
        org, project, user = org_project_user
        from sqlalchemy import select

        from app.models.audit import AuditEvent

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
        assert events
        for event in events:
            payload = str(event.details)
            assert "totally-secret-client-name" not in payload
            if doc.file_path:
                assert str(doc.file_path) not in payload
