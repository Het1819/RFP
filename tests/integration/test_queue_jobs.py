import io

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import get_default_org_and_user
from app.models.document import Document, DocumentPage
from app.models.job import ProcessingJob
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.services.ingestion_state import IngestionStatus
from tests.integration.test_csrf import extract_csrf_token


def test_upload_never_creates_legacy_job_even_with_queue_enabled(
    client, db, monkeypatch
):
    """A5b: RFP uploads route through quarantine-first ingestion and stop
    at SCANNING/REJECTED_TYPE - no legacy document_processing job is ever
    enqueued, even when QUEUE_ENABLED=True. This supersedes the pre-A5b
    behavior where uploading synchronously created and ran a
    ProcessingJob."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "QUEUE_ENABLED", True)

    # Mock enqueue_to_redis to avoid connecting to real Redis
    async def mock_enqueue(job_id):
        pass

    monkeypatch.setattr("app.core.queue.enqueue_to_redis", mock_enqueue)

    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Upload Queue Project",
        client_name="Client A",
    )
    db.add(proj)
    db.commit()

    # Get CSRF token
    response_get = client.get(f"/projects/{proj.id}")
    csrf_token = extract_csrf_token(response_get.text)

    # Perform upload (don't follow redirects to check 303)
    pdf_content = b"%PDF-1.4\n" + b"x" * 200 + b"\n%%EOF"
    response = client.post(
        f"/projects/{proj.id}/upload",
        data={"csrf_token": csrf_token},
        files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303

    # Check Document status: quarantine-first ingestion, not the legacy
    # pending/processing/completed pipeline.
    doc = db.scalar(
        select(Document).where(
            Document.project_id == proj.id, Document.doc_role == "rfp"
        )
    )
    assert doc is not None
    assert doc.ingestion_status == IngestionStatus.SCANNING

    # No legacy ProcessingJob is created by the upload route anymore.
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == doc.id))
    assert job is None


def test_job_idempotency_prevents_duplication(client, db, monkeypatch):
    """Proves retrying/running a job multiple times doesn't duplicate records."""

    # Mock page extractor at the exact place it is imported/used
    def mock_extract(file_path, file_type):
        return [
            {"page_number": 1, "content": "mock requirement shall exist"},
            {"page_number": 2, "content": "mock requirement should be met"},
        ]

    monkeypatch.setattr("app.services.project_service.extract_pages", mock_extract)

    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Idempotency Project",
        client_name="Client B",
    )
    db.add(proj)
    db.commit()

    doc = Document(
        project_id=proj.id,
        name="idempotency.pdf",
        file_path="mock_path.pdf",
        file_type="application/pdf",
        doc_role="rfp",
        processing_status="pending",
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()

    # Run the job pipeline twice
    from app.core.queue import enqueue_job

    job1 = enqueue_job(
        db=db,
        org_id=org_id,
        project_id=proj.id,
        document_id=doc.id,
        job_type="document_processing",
        user_id=user_id,
        sync_mode=True,
    )
    assert job1.status == "SUCCEEDED"

    pages_all_1 = db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id)
    ).all()
    reqs_all_1 = db.scalars(
        select(Requirement).where(Requirement.source_document_id == doc.id)
    ).all()

    assert len(pages_all_1) == 2
    assert len(reqs_all_1) == 2

    # Reset job attempts/status to test rerun manually via pipeline helper
    import asyncio

    from app.services.project_service import process_job_pipeline_async

    # Reset doc
    doc.processing_status = "pending"
    db.commit()

    job2 = ProcessingJob(
        org_id=org_id,
        project_id=proj.id,
        document_id=doc.id,
        job_type="document_processing",
        status="QUEUED",
        created_by_user_id=user_id,
        attempts=0,
        max_attempts=3,
    )
    db.add(job2)
    db.commit()

    asyncio.run(process_job_pipeline_async(db, job2))
    assert job2.status == "SUCCEEDED"

    pages_all_2 = db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id)
    ).all()
    reqs_all_2 = db.scalars(
        select(Requirement).where(Requirement.source_document_id == doc.id)
    ).all()

    # Assert counts have not duplicated
    assert len(pages_all_2) == 2
    assert len(reqs_all_2) == 2


def test_failed_processing_logs_safe_error(client, db, monkeypatch):
    """Proves that a failed job does not store raw stack trace in safe_error_message."""
    # Prevent db.rollback from rolling back the test's transactional session
    monkeypatch.setattr(db, "rollback", lambda: None)

    def mock_extract_fail(file_path, file_type):
        raise ValueError("Sensitive raw database schema details in error!")

    monkeypatch.setattr("app.services.project_service.extract_pages", mock_extract_fail)

    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Fail Project",
        client_name="Client C",
    )
    db.add(proj)
    db.commit()

    doc = Document(
        project_id=proj.id,
        name="fail.pdf",
        file_path="mock_path.pdf",
        file_type="application/pdf",
        doc_role="rfp",
        processing_status="pending",
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()

    # We set max_attempts to 1 so it fails immediately without retrying
    job = ProcessingJob(
        org_id=org_id,
        project_id=proj.id,
        document_id=doc.id,
        job_type="document_processing",
        status="QUEUED",
        created_by_user_id=user_id,
        attempts=0,
        max_attempts=1,
    )
    db.add(job)
    db.commit()

    import asyncio

    from app.services.project_service import process_job_pipeline_async

    asyncio.run(process_job_pipeline_async(db, job))

    assert job.status == "FAILED"
    assert job.error_type == "ValueError"
    assert "Sensitive raw database schema" not in job.safe_error_message
    assert "Failed during step" in job.safe_error_message


def test_foreign_org_validation_for_jobs_and_retry(client, db, monkeypatch):
    """Proves a foreign organization user cannot view project jobs or request retry."""
    # Create target org and user
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Target Project",
        client_name="Target Client",
    )
    db.add(proj)
    db.commit()

    doc = Document(
        project_id=proj.id,
        name="target.pdf",
        file_path="mock_path.pdf",
        file_type="application/pdf",
        doc_role="rfp",
        processing_status="failed",
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()

    # Create foreign org and user
    from app.models.organization import Organization
    from app.models.user import User

    foreign_org = Organization(name="Foreign Org")
    db.add(foreign_org)
    db.commit()

    foreign_user = User(
        organization_id=foreign_org.id,
        email="foreign@example.com",
        full_name="Foreign User",
        hashed_password="mock_password",
    )
    db.add(foreign_user)
    db.commit()

    monkeypatch.setattr(
        "app.web.routes.projects.get_current_org_and_user",
        lambda r, d: (foreign_org.id, foreign_user.id),
    )

    # Re-import client
    response_jobs = client.get(f"/projects/{proj.id}/jobs")
    assert (
        response_jobs.status_code == 404
    )  # Not found due to project ownership validation

    response_retry = client.post(f"/projects/{proj.id}/documents/{doc.id}/retry")
    assert response_retry.status_code == 404


def test_redis_url_enforcement_in_production(monkeypatch):
    """Proves that a production environment requires REDIS_URL when queue is enabled."""
    # Test setting validation
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            QUEUE_ENABLED=True,
            REDIS_URL="",
        )
    assert "REDIS_URL must be configured" in str(excinfo.value)
