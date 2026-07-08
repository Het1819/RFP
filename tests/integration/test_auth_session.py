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


def test_unauthenticated_get_redirects_to_login(unauthenticated_client, monkeypatch):
    """Proves unauthenticated GET requests to protected pages redirect to /login."""
    from app.core.config import settings

    # Force session mode so dev auth fallback does not fire
    monkeypatch.setattr(settings, "AUTH_MODE", "session")
    response = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unauthenticated_mutating_post_fails_closed(
    unauthenticated_client, db, monkeypatch
):
    """Proves unauthenticated POST requests do not create data and return 401."""
    from app.core.config import settings

    # Force session mode so dev auth fallback does not fire
    monkeypatch.setattr(settings, "AUTH_MODE", "session")
    response = unauthenticated_client.post(
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


def test_authenticated_client_can_access_projects(client):
    """Proves the authenticated client fixture can access /projects (regression)."""
    response = client.get("/projects")
    assert response.status_code == 200


def test_unauthenticated_client_cannot_access_projects(
    unauthenticated_client, monkeypatch
):
    """Proves unauthenticated client is blocked from /projects (regression)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "session")
    response = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dev_auth_mode_login(unauthenticated_client, db):
    """Proves dev AUTH_MODE login generates an explicit user session."""
    # Seed session
    get_resp = unauthenticated_client.get("/login")
    assert get_resp.status_code == 200
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)

    # Perform dev login with a seeded active user email
    response = unauthenticated_client.post(
        "/login",
        data={"email": "dev-user@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects"

    # Verify access is now granted
    proj_resp = unauthenticated_client.get("/projects")
    assert proj_resp.status_code == 200


def test_logout_clears_session(unauthenticated_client):
    """Proves logging out clears user credentials and blocks access."""
    # 1. Login first
    get_resp = unauthenticated_client.get("/login")
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)
    unauthenticated_client.post(
        "/login",
        data={"email": "logout-test@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    # Verify access allowed
    assert unauthenticated_client.get("/projects").status_code == 200

    # 2. Logout
    logout_resp = unauthenticated_client.get("/logout", follow_redirects=False)
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/login"

    # 3. Verify access is blocked (force session mode — no dev fallback)
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = unauthenticated_client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    finally:
        settings.AUTH_MODE = original_mode


def test_invalid_deleted_user_fails_closed(unauthenticated_client, db):
    """Proves session with deactivated/missing user fails closed."""
    # 1. Login
    get_resp = unauthenticated_client.get("/login")
    from tests.integration.test_csrf import extract_csrf_token

    csrf_token = extract_csrf_token(get_resp.text)
    unauthenticated_client.post(
        "/login",
        data={"email": "deactivate@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    assert unauthenticated_client.get("/projects").status_code == 200

    # 2. Deactivate the user in DB
    user = db.scalars(
        select(User).where(User.email == "deactivate@rfparchitect.com")
    ).first()
    assert user is not None
    user.is_active = False
    db.commit()

    # 3. Verify accessing projects fails closed (force session mode)
    from app.core.config import settings

    original_mode = settings.AUTH_MODE
    settings.AUTH_MODE = "session"
    try:
        response = unauthenticated_client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    finally:
        settings.AUTH_MODE = original_mode


def test_auth_fixture_does_not_depend_on_local_env(client):
    """Proves client fixture is CI-independent: authenticated without local .env."""
    # If AUTH_MODE=dev is set via CI env and not from .env, client still works.
    from app.core.config import settings

    assert settings.AUTH_MODE == "dev", (
        "AUTH_MODE must be 'dev' in test environment. "
        "Set AUTH_MODE=dev in CI env or local .env."
    )
    response = client.get("/projects")
    assert response.status_code == 200
