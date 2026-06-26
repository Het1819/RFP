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
    event = AuditEvent(
        organization_id=org_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
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
    background_tasks: BackgroundTasks,
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
        processing_status="processing",  # Immediate processing state
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

    # 6. Queue background extraction task
    from app.core.database import SessionLocal

    background_tasks.add_task(process_document_background, SessionLocal, doc.id)

    return doc


def process_document_background(
    db_session_factory: Any, document_id: uuid.UUID
) -> None:
    """Background task to extract text and update status."""
    db = db_session_factory()
    try:
        doc = db.get(Document, document_id)
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

        # Trigger requirement extraction
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
        doc = db.get(Document, document_id)
        if doc:
            doc.processing_status = "failed"
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
