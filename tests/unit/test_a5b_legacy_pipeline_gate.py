"""Task 8: fail-closed ingestion_status gates on retrieval, evidence
validation, and the legacy document-processing pipeline.

Proves:
- retrieve_evidence() excludes documents that have not reached
  IngestionStatus.COMPLETED, even when legacy processing_status/
  approval_status are already "green".
- validate_evidence_candidate() rejects the same documents as evidence
  candidates.
- process_job_pipeline_async() refuses to run the legacy PyMuPDF/
  python-docx extraction pipeline against any document below
  IngestionStatus.CLEAN, even if something enqueued a job for it (defense
  in depth beyond simply "never enqueue" in Task 6/7).
"""

import asyncio

import pytest

from app.models.document import Document, DocumentPage
from app.models.job import ProcessingJob
from app.services.evidence_validation import (
    EvidenceValidationError,
    validate_evidence_candidate,
)
from app.services.ingestion_state import IngestionStatus
from app.services.project_service import process_job_pipeline_async
from app.services.retriever import retrieve_evidence


class TestRetrievalGatedByIngestionStatus:
    def test_scanning_document_excluded_from_retrieval(
        self, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",  # legacy field, deliberately "green"
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(
            DocumentPage(document_id=doc.id, page_number=1, content="needle content")
        )
        db.commit()

        results = retrieve_evidence(db, project.id, "needle")
        assert all(r["doc_id"] != str(doc.id) for r in results)

    def test_completed_document_included_in_retrieval(
        self, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",
            ingestion_status=IngestionStatus.COMPLETED,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(
            DocumentPage(document_id=doc.id, page_number=1, content="needle content")
        )
        db.commit()

        results = retrieve_evidence(db, project.id, "needle")
        assert any(r["doc_id"] == str(doc.id) for r in results)


class TestEvidenceValidationGatedByIngestionStatus:
    def test_scanning_document_rejected_as_evidence(self, db, org_project_user) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",
            ingestion_status=IngestionStatus.SCANNING,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(
            DocumentPage(document_id=doc.id, page_number=1, content="needle content")
        )
        db.commit()

        with pytest.raises(EvidenceValidationError):
            validate_evidence_candidate(
                db,
                requirement_project_id=project.id,
                document_id=doc.id,
                page_number=1,
                client_snippet="needle content",
            )

    def test_completed_document_accepted_as_evidence(
        self, db, org_project_user
    ) -> None:
        _org, project, user = org_project_user
        doc = Document(
            project_id=project.id,
            name="kb.docx",
            file_path="/tmp/kb.docx",
            file_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            doc_role="knowledge_base",
            approval_status="APPROVED",
            processing_status="completed",
            ingestion_status=IngestionStatus.COMPLETED,
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        db.add(
            DocumentPage(document_id=doc.id, page_number=1, content="needle content")
        )
        db.commit()

        canonical_snippet, resolved_page = validate_evidence_candidate(
            db,
            requirement_project_id=project.id,
            document_id=doc.id,
            page_number=1,
            client_snippet="needle content",
        )
        assert canonical_snippet == "needle content"
        assert resolved_page == 1


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
