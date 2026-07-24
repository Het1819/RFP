"""Integration tests for Phase A1 password-based session authentication."""

import pytest
from sqlalchemy import select

from app.core.passwords import hash_password, verify_password
from app.models.organization import Organization
from app.models.user import User
from tests.integration.test_csrf import extract_csrf_token

GENERIC_ERROR = "Invalid%20email%20or%20password"
_TEST_PASSWORD = "correct-horse-battery-staple"


def _create_user(db, email: str, *, password: str | None = None, is_active=True):
    org = Organization(name=f"Org for {email}")
    db.add(org)
    db.commit()
    db.refresh(org)

    hashed = hash_password(password) if password is not None else "malformed-hash"
    user = User(
        organization_id=org.id,
        email=email,
        hashed_password=hashed,
        full_name="Test User",
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return org, user


def _get_login_csrf(client):
    resp = client.get("/login")
    return extract_csrf_token(resp.text)


def _post_login(client, csrf_token, **form):
    return client.post(
        "/login",
        data={"csrf_token": csrf_token, **form},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )


@pytest.fixture(autouse=True)
def _session_mode(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "session")


def test_valid_email_without_password_fails(unauthenticated_client, db):
    _create_user(db, "novalidator@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client, csrf_token, email="novalidator@rfparchitect.com"
    )
    assert resp.status_code == 303
    assert resp.headers["location"] != "/projects"

    proj_resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert proj_resp.status_code == 303
    assert proj_resp.headers["location"] == "/login"


def test_valid_email_wrong_password_fails(unauthenticated_client, db):
    _create_user(db, "wrongpw@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="wrongpw@rfparchitect.com",
        password="totally-wrong-password",
    )
    assert resp.status_code == 303
    assert resp.headers["location"] != "/projects"
    assert GENERIC_ERROR in resp.headers["location"]


def test_unknown_email_fails_with_generic_response(unauthenticated_client, db):
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="does-not-exist@rfparchitect.com",
        password="whatever-password-value",
    )
    assert resp.status_code == 303
    assert GENERIC_ERROR in resp.headers["location"]


def test_inactive_user_fails_with_same_generic_response(unauthenticated_client, db):
    _create_user(
        db, "inactive@rfparchitect.com", password=_TEST_PASSWORD, is_active=False
    )
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="inactive@rfparchitect.com",
        password=_TEST_PASSWORD,
    )
    assert resp.status_code == 303
    assert GENERIC_ERROR in resp.headers["location"]


def test_malformed_stored_hash_fails_closed(unauthenticated_client, db):
    _create_user(db, "malformed@rfparchitect.com", password=None)
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="malformed@rfparchitect.com",
        password="any-password-value",
    )
    assert resp.status_code == 303
    assert resp.headers["location"] != "/projects"


def test_correct_email_and_password_succeeds(unauthenticated_client, db):
    _create_user(db, "gooduser@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="gooduser@rfparchitect.com",
        password=_TEST_PASSWORD,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"

    proj_resp = unauthenticated_client.get("/projects")
    assert proj_resp.status_code == 200


def test_case_insensitive_email_lookup_succeeds(unauthenticated_client, db):
    _create_user(db, "mixedcase@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="  MixedCase@RfpArchitect.com  ",
        password=_TEST_PASSWORD,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"


def test_failed_login_creates_no_authenticated_session(unauthenticated_client, db):
    _create_user(db, "failsession@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    _post_login(
        unauthenticated_client,
        csrf_token,
        email="failsession@rfparchitect.com",
        password="wrong-password",
    )

    proj_resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert proj_resp.status_code == 303
    assert proj_resp.headers["location"] == "/login"


def test_successful_login_grants_projects_access(unauthenticated_client, db):
    _create_user(db, "grantaccess@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)

    _post_login(
        unauthenticated_client,
        csrf_token,
        email="grantaccess@rfparchitect.com",
        password=_TEST_PASSWORD,
    )

    assert unauthenticated_client.get("/projects").status_code == 200


def test_session_and_csrf_state_refreshed_before_auth_established(
    unauthenticated_client, db
):
    """Proves login establishes fresh session/CSRF state rather than reusing
    the pre-authentication session (i.e. session is cleared first)."""
    _create_user(db, "freshsession@rfparchitect.com", password=_TEST_PASSWORD)
    pre_login_csrf_token = _get_login_csrf(unauthenticated_client)

    login_resp = _post_login(
        unauthenticated_client,
        pre_login_csrf_token,
        email="freshsession@rfparchitect.com",
        password=_TEST_PASSWORD,
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/projects"

    post_login_page = unauthenticated_client.get("/projects")
    post_login_csrf_token = extract_csrf_token(post_login_page.text)

    assert post_login_csrf_token != pre_login_csrf_token


def test_login_without_csrf_token_fails(unauthenticated_client, db):
    _create_user(db, "nocsrf@rfparchitect.com", password=_TEST_PASSWORD)

    resp = unauthenticated_client.post(
        "/login",
        data={
            "email": "nocsrf@rfparchitect.com",
            "password": _TEST_PASSWORD,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert resp.status_code == 403


def test_get_logout_is_unavailable(unauthenticated_client, db):
    """GET /logout must not be a valid state-changing route (405), and must
    not clear an authenticated session."""
    _create_user(db, "getlogout@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)
    _post_login(
        unauthenticated_client,
        csrf_token,
        email="getlogout@rfparchitect.com",
        password=_TEST_PASSWORD,
    )
    assert unauthenticated_client.get("/projects").status_code == 200

    get_logout_resp = unauthenticated_client.get("/logout", follow_redirects=False)
    assert get_logout_resp.status_code == 405

    # Session must remain authenticated.
    assert unauthenticated_client.get("/projects").status_code == 200


def test_post_logout_without_csrf_fails(unauthenticated_client, db):
    _create_user(db, "logoutnocsrf@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)
    _post_login(
        unauthenticated_client,
        csrf_token,
        email="logoutnocsrf@rfparchitect.com",
        password=_TEST_PASSWORD,
    )

    resp = unauthenticated_client.post(
        "/logout",
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    # Session must remain authenticated.
    assert unauthenticated_client.get("/projects").status_code == 200


def test_post_logout_with_valid_csrf_clears_session(unauthenticated_client, db):
    _create_user(db, "logoutcsrf@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)
    _post_login(
        unauthenticated_client,
        csrf_token,
        email="logoutcsrf@rfparchitect.com",
        password=_TEST_PASSWORD,
    )

    projects_resp = unauthenticated_client.get("/projects")
    logout_csrf_token = extract_csrf_token(projects_resp.text)

    resp = unauthenticated_client.post(
        "/logout",
        data={"csrf_token": logout_csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    blocked_resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert blocked_resp.status_code == 303
    assert blocked_resp.headers["location"] == "/login"


def test_password_hashing_produces_verifiable_argon2_hash():
    """Proves the shared password utility hashes with Argon2 and verifies
    correctly, failing closed for wrong passwords and malformed hashes."""
    hashed = hash_password("a-reasonably-long-passphrase")
    assert hashed.startswith("$argon2")
    assert verify_password("a-reasonably-long-passphrase", hashed) is True
    assert verify_password("wrong-passphrase", hashed) is False
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", None) is False


def test_password_not_leaked_in_failure_response(unauthenticated_client, db):
    """Proves the submitted password never appears in the login failure
    response body or redirect location."""
    _create_user(db, "noleaks@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)
    secret_password = "super-secret-submitted-password"

    resp = _post_login(
        unauthenticated_client,
        csrf_token,
        email="noleaks@rfparchitect.com",
        password=secret_password,
    )
    assert secret_password not in resp.text
    assert secret_password not in resp.headers.get("location", "")


def test_unknown_and_wrong_password_share_identical_error_response(
    unauthenticated_client, db
):
    """Proves unknown-email and wrong-password failures are indistinguishable
    to the caller."""
    _create_user(db, "shared-error@rfparchitect.com", password=_TEST_PASSWORD)

    csrf_token_1 = _get_login_csrf(unauthenticated_client)
    unknown_resp = _post_login(
        unauthenticated_client,
        csrf_token_1,
        email="never-registered@rfparchitect.com",
        password="whatever",
    )

    csrf_token_2 = _get_login_csrf(unauthenticated_client)
    wrong_pw_resp = _post_login(
        unauthenticated_client,
        csrf_token_2,
        email="shared-error@rfparchitect.com",
        password="wrong-password",
    )

    assert unknown_resp.headers["location"] == wrong_pw_resp.headers["location"]


def test_dev_auth_mode_still_works_in_permitted_environment(
    unauthenticated_client, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "dev")
    monkeypatch.setattr(settings, "APP_ENV", "test")

    resp = unauthenticated_client.get("/login")
    assert resp.status_code == 200

    csrf_token = extract_csrf_token(resp.text)
    login_resp = unauthenticated_client.post(
        "/login",
        data={"email": "dev-only@rfparchitect.com", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/projects"


def test_production_still_refuses_dev_auth_mode():
    from app.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(APP_ENV="production", AUTH_MODE="dev", SESSION_SECRET_KEY="a" * 32)
    assert "AUTH_MODE cannot be 'dev'" in str(exc_info.value)


def test_login_failure_audit_event_excludes_plaintext_password(
    unauthenticated_client, db
):
    """Proves failed-login audit events never contain the submitted
    password."""
    from app.models.audit import AuditEvent

    _org, user = _create_user(db, "audited@rfparchitect.com", password=_TEST_PASSWORD)
    csrf_token = _get_login_csrf(unauthenticated_client)
    secret_password = "extremely-secret-value-12345"

    _post_login(
        unauthenticated_client,
        csrf_token,
        email="audited@rfparchitect.com",
        password=secret_password,
    )

    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == user.id)).all()
    assert len(events) >= 1
    for event in events:
        details_str = str(event.details or {})
        assert secret_password not in details_str
