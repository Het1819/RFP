"""A5b end-to-end route behavior: uploads quarantine, detect, stop at
SCANNING/REJECTED_TYPE, never enqueue legacy processing."""

import io

import pytest

from app.models.document import Document
from app.services.ingestion_state import IngestionStatus


@pytest.fixture(autouse=True)
def _stub_enqueue_scan_job(monkeypatch):
    """These are A5b route-level tests: they assert the upload route
    reaches SCANNING/REJECTED_TYPE and never creates a legacy
    ProcessingJob. As of A5c Task 6, reaching SCANNING also calls
    enqueue_scan_job(), which -- with QUEUE_ENABLED=false, the test-suite
    default -- would otherwise run a real scan attempt synchronously
    in-process (including a real ClamAV socket connection) inside the
    request and mutate the document past SCANNING. Stub it to a no-op so
    these tests keep asserting the A5b route behavior in isolation from
    the scanner (covered separately by test_malware_scan.py and
    test_a5c_worker_wiring.py)."""
    import app.core.queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_scan_job", lambda document_id: None)


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


class TestRfpUploadRowLock:
    def test_first_upload_locks_parent_project_row_on_postgresql(
        self, db, org_project_user, monkeypatch
    ) -> None:
        """A fully concurrent-transaction test isn't feasible under SQLite
        (it has no real row-level FOR UPDATE semantics), so this documents
        and verifies the PostgreSQL-only code path instead: on a project's
        FIRST-ever RFP upload there are zero existing Document rows, so a
        lock scoped to the Document query result set is a no-op (the
        original bug this fixes). The lock must instead target the
        always-present parent ProposalProject row, acquired before the
        Document query, so two concurrent first-uploads serialize on it
        regardless of whether any Document rows exist yet."""
        import io

        from fastapi import UploadFile
        from starlette.datastructures import Headers

        from app.models.project import ProposalProject
        from app.services import project_service

        org, project, user = org_project_user
        assert db.bind is not None
        # Force the PostgreSQL-only locking branch even though the test
        # engine is SQLite; SQLite silently ignores FOR UPDATE at compile
        # time, so the statement can still execute for inspection.
        monkeypatch.setattr(db.bind.dialect, "name", "postgresql")

        executed_statements = []
        original_execute = db.execute

        def spy_execute(statement, *args, **kwargs):
            executed_statements.append(statement)
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", spy_execute)

        upload = UploadFile(
            filename="rfp.pdf",
            file=io.BytesIO(_pdf_bytes()),
            headers=Headers({"content-type": "application/pdf"}),
        )
        project_service.upload_rfp_document(db, project.id, org.id, user.id, upload)

        lock_statements = [
            s
            for s in executed_statements
            if "FOR UPDATE" in str(s) and ProposalProject.__tablename__ in str(s)
        ]
        assert lock_statements, (
            "expected a SELECT ... FOR UPDATE against the ProposalProject "
            "table before the Document query"
        )


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
