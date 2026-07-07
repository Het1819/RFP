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
    return templates.TemplateResponse(
        request=request,
        name="projects/detail.html",
        context={
            "project": project,
            "document": doc,
            "knowledge_docs": knowledge_docs,
            "error_msg": error_msg,
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
    return templates.TemplateResponse(
        request=request,
        name="projects/status_partial.html",
        context={"project": project, "document": doc},
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
        processing_status="processing",
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

    from app.services.project_service import (
        log_audit_event,
        process_document_background,
    )

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="knowledge_upload",
        entity_type="Document",
        entity_id=doc.id,
        details={"name": doc.name, "approval_status": doc.approval_status},
    )

    from app.core.database import SessionLocal

    background_tasks.add_task(process_document_background, SessionLocal, doc.id)

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
