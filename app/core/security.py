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


# Fixed authorization result codes. Logged instead of any user/candidate
# detail, so an authorization decision never leaks tenant or resource state
# into logs.
REVIEW_AUTH_OK = "REVIEW_AUTH_OK"
REVIEW_AUTH_NO_USER = "REVIEW_AUTH_NO_USER"
REVIEW_AUTH_INACTIVE = "REVIEW_AUTH_INACTIVE"
REVIEW_AUTH_TENANT_MISMATCH = "REVIEW_AUTH_TENANT_MISMATCH"
REVIEW_AUTH_NO_CAPABILITY = "REVIEW_AUTH_NO_CAPABILITY"


class ReviewerAuthorizationError(HTTPException):
    """Authorization failure for a requirement-review action.

    Carries a fixed ``result_code`` for auditing while the HTTP detail stays
    generic, so callers can record *why* without telling the client.
    """

    def __init__(self, status_code: int, detail: str, result_code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.result_code = result_code


def require_requirement_reviewer(
    db: Session, user_id: uuid.UUID, org_id: uuid.UUID
) -> User:
    """Authorize `user_id` to review requirement candidates within `org_id`.

    This is the single authority boundary for promoting machine-proposed
    candidates into authoritative Requirements. It is enforced in the service
    layer, not only in route dependencies, so no future caller (worker, CLI,
    another service) can reach the promotion path without passing through here.

    A user must be authenticated, active, a member of `org_id`, and explicitly
    granted `can_review_requirements`. Organization membership alone is not
    sufficient -- that is the distinction between "can see the project" and
    "may approve machine output as authoritative".

    Raises ReviewerAuthorizationError: 404 when the user is not a member of
    `org_id` (non-disclosing, matching get_project_for_org), 401 when the
    account is missing or deactivated, 403 when the member simply lacks the
    capability. Never returns a partially-authorized user.
    """
    user = db.get(User, user_id)
    if not user:
        logger.warning("requirement_review_authorization: %s", REVIEW_AUTH_NO_USER)
        raise ReviewerAuthorizationError(
            status_code=401,
            detail="Authentication required",
            result_code=REVIEW_AUTH_NO_USER,
        )

    if not user.is_active:
        logger.warning(
            "requirement_review_authorization: %s user_id=%s",
            REVIEW_AUTH_INACTIVE,
            user_id,
        )
        raise ReviewerAuthorizationError(
            status_code=401,
            detail="User account is deactivated or missing",
            result_code=REVIEW_AUTH_INACTIVE,
        )

    # Tenant check precedes the capability check so a cross-tenant caller can
    # never distinguish "wrong org" from "no capability".
    if user.organization_id != org_id:
        logger.warning(
            "requirement_review_authorization: %s user_id=%s org_id=%s",
            REVIEW_AUTH_TENANT_MISMATCH,
            user_id,
            org_id,
        )
        raise ReviewerAuthorizationError(
            status_code=404,
            detail="Not found",
            result_code=REVIEW_AUTH_TENANT_MISMATCH,
        )

    if not user.can_review_requirements:
        logger.warning(
            "requirement_review_authorization: %s user_id=%s org_id=%s",
            REVIEW_AUTH_NO_CAPABILITY,
            user_id,
            org_id,
        )
        raise ReviewerAuthorizationError(
            status_code=403,
            detail="Requirement review is not permitted for this account",
            result_code=REVIEW_AUTH_NO_CAPABILITY,
        )

    return user


def get_project_for_org(
    db: Session, project_id: uuid.UUID, org_id: uuid.UUID
) -> ProposalProject:
    """Retrieve project by ID and verify organization ownership."""
    project = db.get(ProposalProject, project_id)
    if not project or project.organization_id != org_id:
        if project and project.organization_id != org_id:
            logger.warning(
                f"Unauthorized access attempt to project {project_id} "
                f"by organization {org_id}"
            )
            try:
                from app.services.project_service import log_audit_event

                log_audit_event(
                    db,
                    org_id=org_id,
                    user_id=None,
                    action="UNAUTHORIZED_ACCESS_ATTEMPT",
                    entity_type="Project",
                    entity_id=project_id,
                    details={"target_org_id": str(project.organization_id)},
                )
            except Exception:
                pass
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
