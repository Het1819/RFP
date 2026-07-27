import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.client_ip import resolve_client_ip
from app.core.config import settings
from app.core.csrf import generate_csrf_token, validate_csrf_token
from app.core.database import get_db
from app.core.observability import MetricsRegistry
from app.core.passwords import verify_password
from app.core.sessions.throttling import (
    LoginThrottle,
    ThrottleStore,
    ThrottleStoreUnavailableError,
    normalize_email,
)
from app.core.templates import templates
from app.models.organization import Organization
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


def _record_throttle_decision(decision: str) -> None:
    MetricsRegistry.login_throttle_decisions[decision] = (
        MetricsRegistry.login_throttle_decisions.get(decision, 0) + 1
    )


def _build_login_throttle(request: Request) -> LoginThrottle:
    store = cast(ThrottleStore, request.app.state.throttle_store)
    return LoginThrottle(
        store,
        settings.effective_login_throttle_secret,
        account_ip_max=settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX,
        account_ip_window_seconds=settings.LOGIN_THROTTLE_ACCOUNT_IP_WINDOW_SECONDS,
        ip_max=settings.LOGIN_THROTTLE_IP_MAX,
        ip_window_seconds=settings.LOGIN_THROTTLE_IP_WINDOW_SECONDS,
        account_max=settings.LOGIN_THROTTLE_ACCOUNT_MAX,
        account_window_seconds=settings.LOGIN_THROTTLE_ACCOUNT_WINDOW_SECONDS,
        max_cooldown_seconds=settings.LOGIN_THROTTLE_MAX_COOLDOWN_SECONDS,
    )


def _source_ip(request: Request) -> str:
    # Centralized trusted-proxy-aware resolution (app.core.client_ip):
    # forwarded headers are honored only when the direct ASGI peer is the
    # exact configured Nginx backend IP; otherwise the direct peer address
    # is used and forwarded headers are ignored entirely.
    return resolve_client_ip(request, settings.effective_trusted_proxy_ips)


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
async def login_action(
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

        normalized_email = normalize_email(email)
        source_ip = _source_ip(request)
        throttle = _build_login_throttle(request)

        try:
            decision = await throttle.check(
                normalized_email=normalized_email, source_ip=source_ip
            )
        except ThrottleStoreUnavailableError:
            _record_throttle_decision("store_unavailable")
            raise HTTPException(
                status_code=503, detail="Service temporarily unavailable"
            ) from None

        if not decision.allowed:
            _record_throttle_decision("blocked")
            # Minimum safe timing-equalization: still run a password
            # verification (against the dummy hash) so a throttled response
            # doesn't time out faster than a normal failure would, without
            # touching the database.
            verify_password(password, None)
            throttled_response = RedirectResponse(
                url="/login?error=Invalid email or password", status_code=303
            )
            throttled_response.headers["Retry-After"] = str(
                decision.retry_after_seconds
            )
            return throttled_response

        normalized_email_lower = normalized_email
        user = db.scalars(
            select(User).where(func.lower(User.email) == normalized_email_lower)
        ).first()

        stored_hash = (
            user.hashed_password if user is not None and user.is_active else None
        )
        # Always perform password verification work, even for unknown/inactive
        # users, so response timing does not reveal account existence.
        password_ok = verify_password(password, stored_hash)

        from app.services.project_service import log_audit_event

        if user is None or not user.is_active or not password_ok:
            try:
                await throttle.record_failure(
                    normalized_email=normalized_email, source_ip=source_ip
                )
            except ThrottleStoreUnavailableError:
                pass
            _record_throttle_decision("failure_recorded")
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

        try:
            await throttle.record_success(
                normalized_email=normalized_email, source_ip=source_ip
            )
        except ThrottleStoreUnavailableError:
            pass
        _record_throttle_decision("allowed")

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
