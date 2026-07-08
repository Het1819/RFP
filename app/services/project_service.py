import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject
from app.services.extractor import extract_pages, validate_uploaded_file


def log_audit_event(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    """Helper to log audit events to the database."""
    from app.core.observability import request_id_var

    event = AuditEvent(
        organization_id=org_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        request_id=request_id_var.get(),
    )
    db.add(event)
    db.commit()


def get_projects(db: Session, org_id: uuid.UUID) -> list[ProposalProject]:
    """Retrieve all projects for an organization, ordered by created_at desc."""
    return list(
        db.scalars(
            select(ProposalProject)
            .where(ProposalProject.organization_id == org_id)
            .order_by(ProposalProject.created_at.desc())
        ).all()
    )


def get_project(
    db: Session, project_id: uuid.UUID, org_id: uuid.UUID
) -> ProposalProject | None:
    """Retrieve a specific project by ID and organization."""
    return db.scalars(
        select(ProposalProject).where(
            ProposalProject.id == project_id,
            ProposalProject.organization_id == org_id,
        )
    ).first()


def get_project_document(db: Session, project_id: uuid.UUID) -> Document | None:
    """Retrieve the RFP document for a project if it exists."""
    return db.scalars(
        select(Document).where(
            Document.project_id == project_id, Document.doc_role == "rfp"
        )
    ).first()


def create_project(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    client_name: str,
    due_date: datetime | None = None,
) -> ProposalProject:
    """Create a new proposal project and log an audit event."""
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name=name,
        client_name=client_name,
        due_date=due_date,
        status="draft",
    )
    db.add(proj)
    db.commit()
    db.refresh(proj)

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="project_create",
        entity_type="ProposalProject",
        entity_id=proj.id,
        details={"name": name, "client_name": client_name},
    )
    return proj


def upload_rfp_document(
    db: Session,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    file: UploadFile,
    background_tasks: BackgroundTasks | None = None,
) -> Document:
    """Validate and upload an RFP document for a project, then queue text extraction."""
    # 1. Fetch project
    proj = get_project(db, project_id, org_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # 2. Check if project already has an RFP
    existing_rfp = get_project_document(db, project_id)
    if existing_rfp:
        raise HTTPException(
            status_code=400, detail="Project already has an RFP document"
        )

    # 3. Validate file
    validate_uploaded_file(file, settings.MAX_UPLOAD_SIZE)

    # 4. Save file locally
    storage_dir = Path(settings.LOCAL_STORAGE_PATH) / "documents"
    storage_dir.mkdir(parents=True, exist_ok=True)

    doc_id = uuid.uuid4()
    ext = Path(file.filename or "").suffix.lower()
    file_path = storage_dir / f"{doc_id}{ext}"

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 5. Create Document record
    doc = Document(
        id=doc_id,
        project_id=project_id,
        name=file.filename or "RFP Document",
        file_path=str(file_path),
        file_type=file.content_type or "application/octet-stream",
        doc_role="rfp",
        processing_status="pending",  # Starts as pending
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="document_upload",
        entity_type="Document",
        entity_id=doc.id,
        details={"name": doc.name, "role": doc.doc_role},
    )

    # 6. Queue background extraction task via enqueue_job
    from app.core.queue import enqueue_job

    enqueue_job(
        db=db,
        org_id=org_id,
        project_id=project_id,
        document_id=doc.id,
        job_type="document_processing",
        user_id=user_id,
    )

    return doc


def process_document_background(
    db_session_factory: Any, document_id: uuid.UUID
) -> None:
    """Background task to extract text and update status."""
    db = db_session_factory()
    try:
        doc = db.execute(
            select(Document).where(Document.id == document_id).with_for_update()
        ).scalar_one_or_none()
        if not doc:
            return

        # Extract pages
        file_path = Path(doc.file_path)
        pages_data = extract_pages(file_path, doc.file_type)

        # Create DocumentPages and concat full text
        full_text_list = []
        for p in pages_data:
            page = DocumentPage(
                document_id=doc.id,
                page_number=p["page_number"],
                content=p["content"],
            )
            db.add(page)
            full_text_list.append(p["content"])

        doc.content = "\n".join(full_text_list)
        doc.processing_status = "completed"
        db.commit()

        # Trigger requirement extraction only for RFP documents
        if doc.doc_role == "rfp":
            from app.services.extraction_service import run_extraction_sync

            run_extraction_sync(db, doc.id)

        # Log Success
        proj = db.get(ProposalProject, doc.project_id)
        log_audit_event(
            db,
            org_id=proj.organization_id,
            user_id=doc.created_by_id,
            action="document_extraction_success",
            entity_type="Document",
            entity_id=doc.id,
            details={"page_count": len(pages_data)},
        )

    except Exception as e:
        db.rollback()
        # Fetch document again to update status to failed
        doc = db.execute(
            select(Document).where(Document.id == document_id).with_for_update()
        ).scalar_one_or_none()
        if doc:
            doc.processing_status = "failed"
            doc.processing_error = str(e)[:500]
            db.commit()

            proj = db.get(ProposalProject, doc.project_id)
            log_audit_event(
                db,
                org_id=proj.organization_id,
                user_id=doc.created_by_id,
                action="document_extraction_failed",
                entity_type="Document",
                entity_id=doc.id,
                details={"error": str(e)},
            )
    finally:
        db.close()


def run_job_sync(job_id: uuid.UUID) -> None:
    """Helper to run the document processing job pipeline synchronously."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        from app.models.job import ProcessingJob

        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id))
        if not job:
            return

        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(process_job_pipeline_async(db, job))
        finally:
            loop.close()
    finally:
        db.close()


async def process_job_pipeline_async(db: Session, job: Any) -> None:
    """Core job pipeline implementing page and requirement extraction."""
    from datetime import UTC, datetime

    from sqlalchemy import delete

    # 1. Update initial state
    job.attempts += 1
    job.status = "RUNNING"
    job.started_at = datetime.now(UTC)
    job.progress_percent = 10
    job.current_step = "Fetching document record"
    db.commit()

    try:
        # 2. Fetch document
        doc = db.scalar(select(Document).where(Document.id == job.document_id))
        if not doc:
            raise ValueError("Associated document record not found")

        doc.processing_status = "processing"
        db.commit()

        # 3. Extract pages
        job.progress_percent = 30
        job.current_step = "Extracting pages from file"
        db.commit()

        file_path = Path(doc.file_path)
        pages_data = extract_pages(file_path, doc.file_type)

        # 4. Save pages (idempotent)
        job.progress_percent = 50
        job.current_step = "Indexing and saving pages (idempotent)"
        db.commit()

        db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))

        full_text_list = []
        for p in pages_data:
            page = DocumentPage(
                document_id=doc.id,
                page_number=p["page_number"],
                content=p["content"],
            )
            db.add(page)
            full_text_list.append(p["content"])

        doc.content = "\n".join(full_text_list)
        db.commit()

        # 5. Extract requirements if RFP
        if doc.doc_role == "rfp":
            job.progress_percent = 70
            job.current_step = "Extracting requirements from RFP"
            db.commit()

            from app.models.requirement import Requirement

            db.execute(
                delete(Requirement).where(Requirement.source_document_id == doc.id)
            )

            from app.services.extraction_service import (
                extract_requirements_from_document,
            )

            await extract_requirements_from_document(db, doc.id)

        # 6. Complete successfully
        job.progress_percent = 100
        job.current_step = "Completed successfully"
        job.status = "SUCCEEDED"
        job.finished_at = datetime.now(UTC)

        doc.processing_status = "completed"
        doc.processing_error = None
        db.commit()

        # Audit log success
        log_audit_event(
            db,
            org_id=job.org_id,
            user_id=job.created_by_user_id,
            action="document_extraction_success",
            entity_type="Document",
            entity_id=doc.id,
            details={"page_count": len(pages_data)},
        )

    except Exception as e:
        db.rollback()
        error_type = type(e).__name__
        safe_error_msg = f"Failed during step '{job.current_step}': {error_type}"

        if job.attempts < job.max_attempts:
            job.status = "RETRYING"
            job.safe_error_message = safe_error_msg
            job.error_type = error_type
            db.commit()
            if settings.QUEUE_ENABLED:
                from arq import Retry

                raise Retry(defer=settings.JOB_RETRY_BACKOFF_SECONDS) from None
        else:
            job.status = "FAILED"
            job.finished_at = datetime.now(UTC)
            job.safe_error_message = safe_error_msg
            job.error_type = error_type

            if job.document_id:
                doc = db.scalar(select(Document).where(Document.id == job.document_id))
                if doc:
                    doc.processing_status = "failed"
                    doc.processing_error = safe_error_msg
            db.commit()

            # Log failure audit event
            if job.document_id:
                proj = (
                    db.get(ProposalProject, job.project_id) if job.project_id else None
                )
                org_id = proj.organization_id if proj else job.org_id
                log_audit_event(
                    db,
                    org_id=org_id,
                    user_id=job.created_by_user_id,
                    action="document_extraction_failed",
                    entity_type="Document",
                    entity_id=job.document_id,
                    details={"error": safe_error_msg},
                )
