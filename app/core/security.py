import logging
import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse
from app.models.user import User

logger = logging.getLogger(__name__)


def check_app_env_auth() -> None:
    """Helper to verify APP_ENV and block default auth outside dev/test/local."""
    if settings.APP_ENV not in ("development", "local", "test"):
        if settings.AUTH_MODE == "dev":
            raise HTTPException(
                status_code=401,
                detail="Authentication required in non-development environments",
            )


def get_current_org_and_user(
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Returns current organization ID and user ID from the session.
    Fails closed with 401 if no session is present, user is inactive, or mismatch.
    """
    session_user_id_str = request.session.get("user_id")
    session_org_id_str = request.session.get("org_id")

    if not session_user_id_str or not session_org_id_str:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        user_id = uuid.UUID(session_user_id_str)
        org_id = uuid.UUID(session_org_id_str)
    except ValueError as e:
        raise HTTPException(
            status_code=401, detail="Invalid session session IDs"
        ) from e

    # Session integrity checks:
    # 1. Verify user exists
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401, detail="User account is deactivated or missing"
        )

    # 2. Verify org exists
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=401, detail="Organization is missing")

    # 3. Verify user belongs to org
    if user.organization_id != org_id:
        raise HTTPException(status_code=401, detail="User organization mismatch")

    return org_id, user_id


def get_project_for_org(
    db: Session, project_id: uuid.UUID, org_id: uuid.UUID
) -> ProposalProject:
    """Retrieve project by ID and verify organization ownership."""
    project = db.get(ProposalProject, project_id)
    if not project or project.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_requirement_for_org(
    db: Session, requirement_id: uuid.UUID, org_id: uuid.UUID
) -> Requirement:
    """Retrieve requirement by ID and verify project/organization ownership."""
    req = db.get(Requirement, requirement_id)
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    # Verify the project belongs to the current organization
    get_project_for_org(db, req.project_id, org_id)
    return req


def get_document_for_org(
    db: Session, document_id: uuid.UUID, org_id: uuid.UUID
) -> Document:
    """Retrieve document by ID and verify project/organization ownership."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Verify the project belongs to the current organization
    get_project_for_org(db, doc.project_id, org_id)
    return doc


def get_draft_for_org(
    db: Session, draft_id: uuid.UUID, org_id: uuid.UUID
) -> DraftResponse:
    """Retrieve draft by ID and verify requirement/project/organization ownership."""
    draft = db.get(DraftResponse, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    # Verify the requirement belongs to the current organization
    get_requirement_for_org(db, draft.requirement_id, org_id)
    return draft
