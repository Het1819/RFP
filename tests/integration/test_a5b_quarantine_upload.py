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
        _org, project, _user = org_project_user
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        doc = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").one()
        assert doc.ingestion_status == IngestionStatus.SCANNING

    def test_invalid_type_rejected(self, client, db, org_project_user) -> None:
        _org, project, _user = org_project_user
        resp = client.post(
            f"/projects/{project.id}/upload",
            files={
                "file": ("rfp.pdf", b"not a pdf at all just words", "application/pdf")
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        doc = db.query(Document).filter_by(project_id=project.id, doc_role="rfp").one()
        assert doc.ingestion_status == IngestionStatus.REJECTED_TYPE

    def test_rejected_rfp_can_be_replaced(self, client, db, org_project_user) -> None:
        _org, project, _user = org_project_user
        client.post(
            f"/projects/{project.id}/upload",
            files={
                "file": (
                    "rfp.pdf",
                    b"garbage not a pdf padded out",
                    "application/pdf",
                )
            },
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
        _org, project, _user = org_project_user
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

    def test_no_legacy_processing_job_created(
        self, client, db, org_project_user
    ) -> None:
        _org, project, _user = org_project_user
        from app.models.job import ProcessingJob

        client.post(
            f"/projects/{project.id}/upload",
            files={"file": ("rfp.pdf", _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert db.query(ProcessingJob).count() == 0


class TestKnowledgeUploadRoute:
    def test_valid_docx_reaches_scanning(self, client, db, org_project_user) -> None:
        _org, project, _user = org_project_user
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
        _org, project, _user = org_project_user
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
