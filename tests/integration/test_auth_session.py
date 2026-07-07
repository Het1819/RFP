import pytest
from sqlalchemy import select

from app.models.project import ProposalProject
from app.models.user import User


def test_production_fails_with_dev_auth():
    """Proves that production environments cannot start with AUTH_MODE=dev."""
    from app.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(APP_ENV="production", AUTH_MODE="dev", SESSION_SECRET_KEY="a" * 32)
    assert "AUTH_MODE cannot be 'dev'" in str(exc_info.value)


def test_production_fails_without_strong_secret():
    """Proves production environments require a strong SESSION_SECRET_KEY."""
    from app.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(APP_ENV="production", AUTH_MODE="session", SESSION_SECRET_KEY="weak")
    assert "SESSION_SECRET_KEY must be set" in str(exc_info.value)


def test_oidc_fails_with_missing_config():
    """Proves startup fails if OIDC config is missing when AUTH_MODE=oidc."""
    from app.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            AUTH_MODE="oidc",
            SESSION_SECRET_KEY="a" * 32,
            OIDC_ISSUER_URL=None,
        )
    assert "OIDC_ISSUER_URL is required" in str(exc_info.value)


def test_unauthenticated_get_redirects_to_login(client):
    """Proves unauthenticated GET requests to protected pages redirect to /login."""
    # Temporarily set AUTH_MODE to session to disable dev auth silent fallback
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    finally:
        settings.AUTH_MODE = original_mode


def test_unauthenticated_mutating_post_fails_closed(client, db):
    """Proves unauthenticated POST requests do not create data and return 401."""
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = client.post(
            "/projects",
            data={"name": "Attacker Project", "client_name": "Target"},
            follow_redirects=False,
        )
        assert response.status_code == 401

        # Verify no project named Attacker Project was created
        proj = db.scalars(
            select(ProposalProject).where(ProposalProject.name == "Attacker Project")
        ).first()
        assert proj is None
    finally:
        settings.AUTH_MODE = original_mode


def test_dev_auth_mode_login(client, db):
    """Proves dev AUTH_MODE login generates an explicit user session."""
    # Seed session
    get_resp = client.get("/login")
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)

    # Perform dev login
    response = client.post(
        "/login",
        data={"email": "dev-user@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects"

    # Verify access is now granted
    proj_resp = client.get("/projects")
    assert proj_resp.status_code == 200


def test_logout_clears_session(client):
    """Proves logging out clears user credentials and blocks access."""
    # 1. Login first
    get_resp = client.get("/login")
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)
    client.post(
        "/login",
        data={"email": "logout-test@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    # Verify access allowed
    assert client.get("/projects").status_code == 200

    # 2. Logout
    logout_resp = client.get("/logout", follow_redirects=False)
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/login"

    # 3. Verify access is blocked (session AUTH_MODE)
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    finally:
        settings.AUTH_MODE = original_mode


def test_invalid_deleted_user_fails_closed(client, db):
    """Proves session with deactivated/missing user fails closed."""
    # 1. Login
    get_resp = client.get("/login")
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)
    client.post(
        "/login",
        data={"email": "deactivate@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    assert client.get("/projects").status_code == 200

    # 2. Deactivate the user in DB
    user = db.scalars(
        select(User).where(User.email == "deactivate@rfparchitect.com")
    ).first()
    assert user is not None
    user.is_active = False
    db.commit()

    # 3. Verify accessing projects fails closed (gets redirected to login)
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    finally:
        settings.AUTH_MODE = original_mode
