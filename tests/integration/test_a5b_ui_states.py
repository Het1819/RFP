"""Integration tests for A5b quarantine-aware UI states (Task 10).

Verifies that the project status partial renders safe, fixed-string
messages for the new `ingestion_status` values instead of leaking
internal paths, exception text, or claiming a scan happened that did
not, and that the knowledge-upload form no longer exposes a
client-settable `approval_status` selector.
"""

from __future__ import annotations

from app.models.document import Document
from app.services.ingestion_state import IngestionStatus


class TestQuarantineUiStates:
    def test_quarantined_status_shows_upload_received(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=IngestionStatus.QUARANTINED,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}/status")

        assert resp.status_code == 200
        assert "Upload received" in resp.text
        assert str(doc.file_path) not in resp.text

    def test_validating_status_shows_validating_message(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=IngestionStatus.VALIDATING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}/status")

        assert resp.status_code == 200
        assert "Validating document type" in resp.text
        assert str(doc.file_path) not in resp.text

    def test_scanning_status_shows_awaiting_scan(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}/status")

        assert resp.status_code == 200
        assert "security scan" in resp.text.lower()
        assert "malware" not in resp.text.lower() or "awaiting" in resp.text.lower()
        assert "verified safe" not in resp.text.lower()
        assert "scanning..." not in resp.text.lower()

    def test_rejected_type_shows_safe_message(
        self, client, db, org_project_user, monkeypatch
    ) -> None:
        # `project_service.get_project_document` intentionally treats
        # REJECTED_TYPE as a terminal, non-"active" state and filters it
        # out (a project just looks like it has no document, and the
        # upload form re-appears) -- see project_service.py. That's
        # correct/safe behavior on its own, but it means the REJECTED_TYPE
        # branch in status_partial.html can only be exercised directly by
        # bypassing that filter, which is what this test does: it proves
        # that *if* a REJECTED_TYPE document is ever handed to the
        # template (now or by a future change), the template itself does
        # not leak the raw processing_error/file_path and shows the safe
        # fixed string.
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=IngestionStatus.REJECTED_TYPE,
            processing_error="Traceback (most recent call last): raw internal error",
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        import app.services.project_service as project_service

        monkeypatch.setattr(
            project_service, "get_project_document", lambda _db, _pid: doc
        )

        resp = client.get(f"/projects/{project.id}/status")

        assert resp.status_code == 200
        assert "valid PDF or DOCX" in resp.text
        assert "Traceback" not in resp.text
        assert str(doc.file_path) not in resp.text

    def test_legacy_unverified_shows_reprocessing_message(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            ingestion_status=IngestionStatus.LEGACY_UNVERIFIED,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}/status")

        assert resp.status_code == 200
        assert "security reprocessing" in resp.text.lower()

    def test_knowledge_upload_form_has_no_approval_selector(
        self, client, db, org_project_user
    ) -> None:
        _org, project, _user = org_project_user

        resp = client.get(f"/projects/{project.id}")

        assert resp.status_code == 200
        assert 'name="approval_status"' not in resp.text

    def test_compliance_matrix_button_hidden_when_ingestion_not_completed(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="x.pdf",
            file_path="/tmp/x.pdf",
            file_type="application/pdf",
            doc_role="rfp",
            processing_status="completed",
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}")

        assert resp.status_code == 200
        assert "Open Compliance Matrix" not in resp.text

    def test_rejected_type_knowledge_doc_not_shown_as_ordinary_pending(
        self, client, db, org_project_user
    ) -> None:
        # Knowledge documents go through the same ingest_uploaded_document()
        # pipeline as the RFP document, but the `knowledge_docs` query in
        # projects.py has no ingestion_status filter (unlike
        # get_project_document for the RFP), so a REJECTED_TYPE knowledge
        # row IS rendered in the "Uploaded Materials" list. Every new
        # knowledge doc is also forced to approval_status="PENDING" at
        # upload time, so without an ingestion_status-aware badge a
        # rejected upload would be indistinguishable from an ordinary
        # PENDING document awaiting review.
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="rejected.pdf",
            file_path="/tmp/rejected.pdf",
            file_type="application/pdf",
            doc_role="knowledge_base",
            approval_status="PENDING",
            ingestion_status=IngestionStatus.REJECTED_TYPE,
            processing_error="Traceback (most recent call last): raw internal error",
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()

        resp = client.get(f"/projects/{project.id}")

        assert resp.status_code == 200
        assert "Document rejected" in resp.text
        assert "Traceback" not in resp.text
        assert str(doc.file_path) not in resp.text

    def test_scanning_knowledge_doc_distinguishable_from_completed_pending(
        self, client, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        scanning_doc = Document(
            project_id=project.id,
            name="scanning.pdf",
            file_path="/tmp/scanning.pdf",
            file_type="application/pdf",
            doc_role="knowledge_base",
            approval_status="PENDING",
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        completed_doc = Document(
            project_id=project.id,
            name="completed.pdf",
            file_path="/tmp/completed.pdf",
            file_type="application/pdf",
            doc_role="knowledge_base",
            approval_status="PENDING",
            ingestion_status=IngestionStatus.COMPLETED,
            created_by_id=user.id,
        )
        db.add_all([scanning_doc, completed_doc])
        db.commit()

        resp = client.get(f"/projects/{project.id}")

        assert resp.status_code == 200
        # The scanning doc must show the safe ingestion-status label...
        assert "Awaiting security scan" in resp.text
        # ...and the completed doc must still show its ordinary review
        # status, distinct from the scanning doc's label.
        assert "PENDING" in resp.text
