import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security as core_security
from app.core.config import settings
from app.core.csrf import validate_csrf_token
from app.core.database import get_db, get_default_org_and_user
from app.core.security import get_project_for_org
from app.core.templates import templates
from app.models.document import Document
from app.services import project_service

logger = logging.getLogger(__name__)


def get_current_org_and_user(
    request: Request, db: Session
) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return core_security.get_current_org_and_user(request, db)
    except HTTPException as e:
        if e.status_code == 401 and settings.AUTH_MODE == "dev":
            core_security.check_app_env_auth()
            logger.warning(
                "Development authentication active. Falling back to default user/org."
            )
            return get_default_org_and_user(db)
        raise


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_class=HTMLResponse)
def list_projects_view(request: Request, db: Session = Depends(get_db)) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    projects_list = project_service.get_projects(db, org_id)
    return templates.TemplateResponse(
        request=request,
        name="projects/list.html",
        context={"projects": projects_list},
    )


@router.post(
    "", response_class=RedirectResponse, dependencies=[Depends(validate_csrf_token)]
)
def create_project_action(
    request: Request,
    name: str = Form(...),
    client_name: str = Form(...),
    due_date: str = Form(None),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)

    parsed_due_date = None
    if due_date:
        try:
            # support formats like "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM"
            if "T" in due_date:
                parsed_due_date = datetime.fromisoformat(due_date)
            else:
                parsed_due_date = datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            pass

    project_service.create_project(
        db, org_id, user_id, name, client_name, parsed_due_date
    )
    return RedirectResponse(url="/projects", status_code=303)


@router.get("/{project_id}", response_class=HTMLResponse)
def project_detail_view(
    request: Request, project_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)

    doc = project_service.get_project_document(db, project.id)
    error_msg = request.query_params.get("error")
    knowledge_docs = db.scalars(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.doc_role == "knowledge_base",
        )
        .order_by(Document.created_at.desc())
    ).all()

    from app.models.job import ProcessingJob

    job = None
    if doc:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == doc.id)
            .order_by(ProcessingJob.created_at.desc())
        )

    return templates.TemplateResponse(
        request=request,
        name="projects/detail.html",
        context={
            "project": project,
            "document": doc,
            "knowledge_docs": knowledge_docs,
            "error_msg": error_msg,
            "job": job,
        },
    )


@router.post(
    "/{project_id}/upload",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def upload_rfp_action(
    request: Request,
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    try:
        project_service.upload_rfp_document(
            db, project_id, org_id, user_id, file, background_tasks
        )
    except HTTPException as e:
        db.rollback()
        return RedirectResponse(
            url=f"/projects/{project_id}?error={e.detail}", status_code=303
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/projects/{project_id}?error={e!s}", status_code=303
        )

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.get("/{project_id}/status", response_class=HTMLResponse)
def project_document_status_partial(
    request: Request, project_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)

    doc = project_service.get_project_document(db, project.id)

    from app.models.job import ProcessingJob

    job = None
    if doc:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == doc.id)
            .order_by(ProcessingJob.created_at.desc())
        )

    return templates.TemplateResponse(
        request=request,
        name="projects/status_partial.html",
        context={"project": project, "document": doc, "job": job},
    )


@router.post(
    "/{project_id}/knowledge",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def upload_knowledge_action(
    request: Request,
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    owner_name: str = Form(None),
    tags: str = Form(None),
    approval_status: str = Form("APPROVED"),
    version: str = Form("1.0"),
    review_date: str = Form(None),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)

    parsed_review_date = None
    if review_date:
        try:
            parsed_review_date = datetime.strptime(review_date, "%Y-%m-%d")
        except ValueError:
            pass

    _ = get_project_for_org(db, project_id, org_id)

    from app.services.extractor import validate_uploaded_file

    validate_uploaded_file(file, settings.MAX_UPLOAD_SIZE)

    storage_dir = Path(settings.LOCAL_STORAGE_PATH) / "documents"
    storage_dir.mkdir(parents=True, exist_ok=True)

    doc_id = uuid.uuid4()
    ext = Path(file.filename or "").suffix.lower()
    file_path = storage_dir / f"{doc_id}{ext}"

    with file_path.open("wb") as buffer:
        import shutil

        shutil.copyfileobj(file.file, buffer)

    doc = Document(
        id=doc_id,
        project_id=project_id,
        name=file.filename or "Knowledge Document",
        file_path=str(file_path),
        file_type=file.content_type or "application/octet-stream",
        doc_role="knowledge_base",
        processing_status="pending",  # Starts as pending
        owner_name=owner_name,
        tags=tags,
        approval_status=approval_status,
        version=version,
        review_date=parsed_review_date,
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    from app.services.project_service import log_audit_event

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="knowledge_upload",
        entity_type="Document",
        entity_id=doc.id,
        details={"name": doc.name, "approval_status": doc.approval_status},
    )

    from app.core.queue import enqueue_job

    enqueue_job(
        db=db,
        org_id=org_id,
        project_id=project_id,
        document_id=doc.id,
        job_type="document_processing",
        user_id=user_id,
    )

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.post(
    "/{project_id}/documents/{document_id}/retry",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def retry_document_processing_action(
    request: Request,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    doc = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.project_id == project_id
        )
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.processing_status = "pending"
    doc.processing_error = None
    db.commit()

    from app.core.queue import enqueue_job

    enqueue_job(
        db=db,
        org_id=org_id,
        project_id=project_id,
        document_id=doc.id,
        job_type="document_processing",
        user_id=user_id,
    )

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.get("/{project_id}/jobs", response_class=HTMLResponse)
def list_project_jobs(
    request: Request,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)

    from app.models.job import ProcessingJob

    jobs = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.project_id == project_id)
        .order_by(ProcessingJob.created_at.desc())
    ).all()

    font_url = (
        "https://fonts.googleapis.com/css2?"
        "family=Outfit:wght@300;400;500;600;700&display=swap"
    )
    back_url = f"/projects/{project.id}"
    intro_desc = (
        "Audit trail for document parsing, indexing, and requirement extraction."
    )

    # Styled HTML response using local class selectors to keep lines short
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Jobs for {project.name}</title>
        <link rel="stylesheet" href="/static/css/style.css">
        <link href="{font_url}" rel="stylesheet">
        <style>
            body {{
                font-family: 'Outfit', sans-serif;
                background: var(--canvas);
                color: var(--text-primary);
                padding: var(--space-xl);
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                display: flex;
                flex-direction: column;
                gap: var(--space-lg);
            }}
            .job-card {{
                margin-top: 0;
                padding: var(--space-md);
                border-color: var(--border-color);
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
            }}
            .flex-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .meta-grid {{
                font-size: 0.85rem;
                color: var(--text-muted);
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 0.5rem;
            }}
            .err-box {{
                background: rgba(180,35,24,0.05);
                border: 1px solid rgba(180,35,24,0.15);
                padding: 0.75rem;
                border-radius: 8px;
                font-size: 0.85rem;
                color: var(--status-needs-evidence);
                font-family: monospace;
                word-break: break-all;
            }}
            .empty-msg {{
                text-align: center;
                padding: var(--space-xl);
                color: var(--text-muted);
            }}
            .btn-retry {{
                padding: 0.4rem 1rem;
                font-size: 0.8rem;
            }}
            .form-retry {{
                margin-top: 0.5rem;
            }}
        </style>
    </head>
    <body>
        <div class="glass-bg"></div>
        <div class="container">
            <a href="{back_url}" class="btn btn-secondary"
               style="width: fit-content; text-decoration: none;">
                &larr; Back
            </a>
            <h1 style="font-size: 1.75rem; font-weight: 800;">Background Jobs Log</h1>
            <p style="color: var(--text-secondary); margin-bottom: var(--space-md);">
                {intro_desc}
            </p>
            
            <div style="display: flex; flex-direction: column; gap: var(--space-md);">
    """
    for j in jobs:
        status_color = "var(--accent)"
        if j.status == "SUCCEEDED":
            status_color = "var(--status-approved)"
        elif j.status == "FAILED":
            status_color = "var(--status-needs-evidence)"
        elif j.status == "RETRYING":
            status_color = "#e67e22"

        retry_form = ""
        if j.status in ("FAILED", "CANCELLED") and j.document_id:
            csrf_tok = request.scope.get("csrf_token", "")
            retry_url = f"/projects/{project.id}/documents/{j.document_id}/retry"
            retry_form = f"""
            <form action="{retry_url}" method="POST" class="form-retry">
                <input type="hidden" name="csrf_token" value="{csrf_tok}">
                <button type="submit" class="btn btn-primary btn-retry">
                    Retry Job
                </button>
            </form>
            """

        created_str = (
            j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "N/A"
        )
        err_div = ""
        if j.safe_error_message:
            err_div = f'<div class="err-box">{j.safe_error_message}</div>'

        job_title = j.job_type.replace("_", " ").title()
        badge_style = (
            f"color: {status_color}; border-color: {status_color}; font-size: 0.75rem;"
        )

        html_content += f"""
                <div class="feature-card job-card">
                    <div class="flex-row">
                        <span style="font-weight: 600;">{job_title}</span>
                        <span class="badge" style="{badge_style}">
                            {j.status}
                        </span>
                    </div>
                    <div class="meta-grid">
                        <span>Attempts: {j.attempts} / {j.max_attempts}</span>
                        <span>Progress: {j.progress_percent or 0}%</span>
                        <span>Step: {j.current_step or "None"}</span>
                        <span>Created: {created_str}</span>
                    </div>
                    {err_div}
                    {retry_form}
                </div>
        """
    if not jobs:
        html_content += """
                <div class="empty-msg">No background jobs found for this project.</div>
        """
    html_content += """
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/{project_id}/documents/{document_id}/status")
def get_document_status_json(
    request: Request,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    doc = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.project_id == project_id
        )
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.models.job import ProcessingJob

    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == document_id)
        .order_by(ProcessingJob.created_at.desc())
    )

    return {
        "document_id": doc.id,
        "processing_status": doc.processing_status,
        "processing_error": doc.processing_error,
        "job": {
            "status": job.status,
            "progress_percent": job.progress_percent,
            "current_step": job.current_step,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "safe_error_message": job.safe_error_message,
        }
        if job
        else None,
    }
