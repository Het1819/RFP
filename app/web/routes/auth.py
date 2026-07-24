import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.csrf import generate_csrf_token, validate_csrf_token
from app.core.database import get_db
from app.core.passwords import verify_password
from app.core.templates import templates
from app.models.organization import Organization
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_view(request: Request) -> Any:
    # If already logged in, redirect to workspace
    if request.session.get("user_id") and request.session.get("org_id"):
        return RedirectResponse(url="/projects", status_code=303)
    error_msg = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"auth_mode": settings.AUTH_MODE, "error_msg": error_msg},
    )


@router.post(
    "/login",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def login_action(
    request: Request,
    email: str = Form(None),
    password: str = Form(None),
    db: Session = Depends(get_db),
) -> Any:
    if settings.AUTH_MODE == "dev":
        # Dev mode allows selecting/creating user
        if not email:
            email = "default@rfparchitect.com"

        # Look up or create organization
        org = db.scalars(select(Organization).limit(1)).first()
        if not org:
            org = Organization(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                name="Default Org",
            )
            db.add(org)
            db.commit()
            db.refresh(org)

        # Look up or create user
        user = db.scalars(select(User).where(User.email == email)).first()
        if not user:
            user = User(
                id=(
                    uuid.UUID("00000000-0000-0000-0000-000000000002")
                    if email == "default@rfparchitect.com"
                    else uuid.uuid4()
                ),
                organization_id=org.id,
                email=email,
                hashed_password="fake-pbkdf2-sha256-hash-for-now",
                full_name="Workspace User",
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        from app.services.project_service import log_audit_event

        request.session["user_id"] = str(user.id)
        request.session["org_id"] = str(org.id)
        logger.warning(f"Development login successful for {email}")
        log_audit_event(
            db,
            org_id=org.id,
            user_id=user.id,
            action="USER_LOGIN_SUCCESS",
            entity_type="User",
            entity_id=user.id,
            details={"auth_mode": "dev"},
        )
        return RedirectResponse(url="/projects", status_code=303)

    elif settings.AUTH_MODE == "session":
        generic_failure = RedirectResponse(
            url="/login?error=Invalid email or password",
            status_code=303,
        )

        if not email or not password:
            return generic_failure

        normalized_email = email.strip().lower()
        user = db.scalars(
            select(User).where(func.lower(User.email) == normalized_email)
        ).first()

        stored_hash = (
            user.hashed_password if user is not None and user.is_active else None
        )
        # Always perform password verification work, even for unknown/inactive
        # users, so response timing does not reveal account existence.
        password_ok = verify_password(password, stored_hash)

        from app.services.project_service import log_audit_event

        if user is None or not user.is_active or not password_ok:
            if user is not None:
                log_audit_event(
                    db,
                    org_id=user.organization_id,
                    user_id=user.id,
                    action="USER_LOGIN_FAILURE",
                    entity_type="User",
                    entity_id=user.id,
                    details={"reason": "invalid_credentials"},
                )
            return generic_failure

        # Clear any pre-authentication session data, then establish fresh
        # session/CSRF state before setting authenticated values.
        request.session.clear()
        generate_csrf_token(request)
        request.session["user_id"] = str(user.id)
        request.session["org_id"] = str(user.organization_id)
        log_audit_event(
            db,
            org_id=user.organization_id,
            user_id=user.id,
            action="USER_LOGIN_SUCCESS",
            entity_type="User",
            entity_id=user.id,
            details={"auth_mode": "session"},
        )
        return RedirectResponse(url="/projects", status_code=303)

    elif settings.AUTH_MODE == "oidc":
        # OIDC not implemented safe response
        raise HTTPException(
            status_code=501,
            detail="OIDC Authentication is not fully configured or implemented",
        )

    return RedirectResponse(url="/login", status_code=303)


@router.post(
    "/logout",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def logout_action(request: Request) -> Any:
    user_id_str = request.session.get("user_id")
    org_id_str = request.session.get("org_id")

    # Clear session values (clears user_id, org_id, and csrf_token)
    request.session.clear()

    if user_id_str and org_id_str:
        try:
            from app.core.database import SessionLocal
            from app.services.project_service import log_audit_event

            db = SessionLocal()
            try:
                log_audit_event(
                    db,
                    org_id=uuid.UUID(org_id_str),
                    user_id=uuid.UUID(user_id_str),
                    action="USER_LOGOUT",
                    entity_type="User",
                    entity_id=uuid.UUID(user_id_str),
                )
            finally:
                db.close()
        except Exception:
            pass

    return RedirectResponse(url="/login", status_code=303)
