import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.csrf import validate_csrf_token
from app.core.database import get_db
from app.core.security import get_current_org_and_user
from app.core.templates import templates
from app.models.feedback import PilotFeedback
from app.models.project import ProposalProject
from app.models.requirement import Requirement

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("", response_class=HTMLResponse)
def feedback_form(
    request: Request,
    project_id: str | None = None,
    requirement_id: str | None = None,
    db: Session = Depends(get_db),
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)

    # Validate ownership of project if provided
    p_uuid = None
    if project_id:
        try:
            p_uuid = uuid.UUID(project_id)
            proj = db.scalar(
                select(ProposalProject).where(
                    ProposalProject.id == p_uuid,
                    ProposalProject.organization_id == org_id,
                )
            )
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
        except ValueError:
            pass

    # Validate ownership of requirement if provided
    r_uuid = None
    if requirement_id:
        try:
            r_uuid = uuid.UUID(requirement_id)
            req = db.scalar(
                select(Requirement)
                .join(ProposalProject)
                .where(
                    Requirement.id == r_uuid,
                    ProposalProject.organization_id == org_id,
                )
            )
            if not req:
                raise HTTPException(status_code=404, detail="Requirement not found")
        except ValueError:
            pass

    csrf_tok = request.scope.get("csrf_token", "")
    return templates.TemplateResponse(
        request=request,
        name="feedback.html",
        context={
            "project_id": str(p_uuid) if p_uuid else "",
            "requirement_id": str(r_uuid) if r_uuid else "",
            "csrf_token": csrf_tok,
        },
    )


@router.post("", dependencies=[Depends(validate_csrf_token)])
def submit_feedback(
    request: Request,
    project_id: str = Form(None),
    requirement_id: str = Form(None),
    category: str = Form(...),
    severity: str = Form(...),
    message: str = Form(...),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)

    # Validation of fields
    valid_categories = {
        "BUG",
        "USABILITY",
        "AI_QUALITY",
        "EVIDENCE",
        "EXPORT",
        "PERFORMANCE",
        "OTHER",
    }
    valid_severities = {"LOW", "MEDIUM", "HIGH", "BLOCKER"}

    if category not in valid_categories:
        raise HTTPException(status_code=400, detail="Invalid category")
    if severity not in valid_severities:
        raise HTTPException(status_code=400, detail="Invalid severity")

    clean_message = message.strip()
    if not clean_message or len(clean_message) > 2000:
        raise HTTPException(
            status_code=400,
            detail="Message must be between 1 and 2000 characters",
        )

    # Validate project ownership if provided
    p_uuid = None
    if project_id and project_id.strip():
        try:
            p_uuid = uuid.UUID(project_id)
            proj = db.scalar(
                select(ProposalProject).where(
                    ProposalProject.id == p_uuid,
                    ProposalProject.organization_id == org_id,
                )
            )
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid project ID") from e

    # Validate requirement ownership if provided
    r_uuid = None
    if requirement_id and requirement_id.strip():
        try:
            r_uuid = uuid.UUID(requirement_id)
            req = db.scalar(
                select(Requirement)
                .join(ProposalProject)
                .where(
                    Requirement.id == r_uuid,
                    ProposalProject.organization_id == org_id,
                )
            )
            if not req:
                raise HTTPException(status_code=404, detail="Requirement not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid requirement ID") from e

    # Create feedback entry
    feedback = PilotFeedback(
        id=uuid.uuid4(),
        project_id=p_uuid,
        requirement_id=r_uuid,
        category=category,
        severity=severity,
        message=clean_message,
        created_by_user_id=user_id,
        organization_id=org_id,
        status="OPEN",
    )

    db.add(feedback)
    db.commit()

    # Log sanitized feedback creation
    import logging

    logging.getLogger(__name__).info(
        f"Pilot feedback created: category={category}, severity={severity}, "
        f"user_id={user_id}, org_id={org_id}"
    )

    # Redirect to project page or workspace
    if p_uuid:
        return RedirectResponse(
            url=f"/projects/{p_uuid}?feedback_success=1", status_code=303
        )
    return RedirectResponse(url="/projects?feedback_success=1", status_code=303)
