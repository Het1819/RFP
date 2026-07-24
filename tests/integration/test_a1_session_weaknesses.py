"""Historical regression evidence: these tests proved the weaknesses of the
Phase A1 client-side session (the now-removed ``SimpleSessionMiddleware``)
BEFORE Phase A2 replaced it with ``ServerSessionMiddleware``.

Recorded result when run against A1 commit bc6c170d07b27ff953bac6a91c1b323f99b3eecf:
all 5 tests PASSED, confirming each vulnerability (cleartext cookie payload,
no server-side session record, copied-cookie-survives-logout, no idle
timeout, no absolute timeout).

They are skipped (not deleted) now that A2 has fixed the underlying
behavior -- running them against ServerSessionMiddleware would fail, which
is the point: it's proof the fix works. The inverted, currently-enforced
versions of these same properties live in
``tests/integration/test_a2_session_security.py``.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.organization import Organization
from app.models.user import User
from tests.integration.test_csrf import extract_csrf_token

pytestmark = pytest.mark.skip(
    reason=(
        "Phase A1 pre-fix evidence only (recorded: 5/5 passed against "
        "bc6c170d07b27ff953bac6a91c1b323f99b3eecf). Superseded by "
        "ServerSessionMiddleware in Phase A2 -- see "
        "test_a2_session_security.py for the enforced, currently-passing "
        "equivalents."
    )
)

_PASSWORD = "correct-horse-battery-staple"
COOKIE_NAME = "rfp_session"


@pytest.fixture(autouse=True)
def _session_mode(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "session")


def _create_active_user(db, email: str):
    from app.core.passwords import hash_password

    org = Organization(name=f"Org for {email}")
    db.add(org)
    db.commit()
    db.refresh(org)

    user = User(
        organization_id=org.id,
        email=email,
        hashed_password=hash_password(_PASSWORD),
        full_name="Weakness Test User",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return org, user


def _login(client: TestClient, email: str) -> None:
    csrf_token = extract_csrf_token(client.get("/login").text)
    resp = client.post(
        "/login",
        data={"csrf_token": csrf_token, "email": email, "password": _PASSWORD},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.headers.get("location") == "/projects"


def _decode_cookie_payload(raw_cookie_value: str) -> dict:
    """Decode the A1 cookie without the server: split off the HMAC signature,
    base64-decode the remainder. This is exactly what an attacker holding a
    stolen cookie can do -- no secret key needed to read the contents."""
    data_b64, _signature = raw_cookie_value.rsplit(".", 1)
    return json.loads(base64.b64decode(data_b64.encode()).decode())


def test_weakness_1_cookie_contains_authentication_data_in_cleartext(
    unauthenticated_client, db
):
    """The A1 cookie is signed but NOT encrypted: user_id, org_id and the CSRF
    token are all readable by anyone holding the cookie (browser extension,
    XSS, proxy log, etc.) without needing the server's secret key."""
    org, user = _create_active_user(db, "cleartext@rfparchitect.com")
    _login(unauthenticated_client, "cleartext@rfparchitect.com")

    raw_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)
    assert raw_cookie is not None

    payload = _decode_cookie_payload(raw_cookie)
    assert payload["user_id"] == str(user.id)
    assert payload["org_id"] == str(org.id)
    assert "csrf_token" in payload


def test_weakness_2_no_server_side_session_record_exists(unauthenticated_client, db):
    """There is no server-side store of issued sessions at all -- the entire
    session lives in the browser. Nothing exists to revoke."""
    _create_active_user(db, "norecord@rfparchitect.com")
    _login(unauthenticated_client, "norecord@rfparchitect.com")

    # A1's only "session store" is the signed cookie itself; there is no
    # Redis (or any) table of active sessions to inspect or delete.
    import app.core.csrf as csrf_module

    assert not hasattr(csrf_module, "SessionStore")
    assert not hasattr(csrf_module, "revoke_session")
    assert not hasattr(csrf_module, "revoke_all_sessions_for_user")


def test_weakness_3_copied_cookie_still_works_after_logout(
    db, monkeypatch, session_store
):
    """A cookie copied to a second client BEFORE logout continues to
    authenticate AFTER the original client logs out. Logout only clears the
    cookie sent back to the client that called /logout -- it cannot reach a
    copy the attacker already has, because no server-side record was ever
    created to invalidate."""
    monkeypatch.setattr(settings, "AUTH_MODE", "session")
    _create_active_user(db, "stolen@rfparchitect.com")

    from app.core.database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            TestClient(app, raise_server_exceptions=True) as victim,
            TestClient(app, raise_server_exceptions=True) as attacker,
        ):
            _login(victim, "stolen@rfparchitect.com")
            stolen_cookie = victim.cookies.get(COOKIE_NAME)
            assert stolen_cookie is not None

            # Attacker copies the raw cookie value onto their own client.
            attacker.cookies.set(COOKIE_NAME, stolen_cookie)
            assert attacker.get("/projects").status_code == 200

            # Victim logs out.
            projects_resp = victim.get("/projects")
            logout_csrf = extract_csrf_token(projects_resp.text)
            logout_resp = victim.post(
                "/logout",
                data={"csrf_token": logout_csrf},
                headers={"X-Test-Enforce-CSRF": "true"},
                follow_redirects=False,
            )
            assert logout_resp.status_code == 303

            # Victim's own client is now logged out.
            assert (
                victim.get(
                    "/projects", headers={"accept": "text/html"}, follow_redirects=False
                ).status_code
                == 303
            )

            # VULNERABILITY: the attacker's copied cookie is untouched by the
            # victim's logout and still authenticates.
            attacker_resp = attacker.get("/projects")
            assert attacker_resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_weakness_4_no_idle_timeout_enforced(unauthenticated_client, db):
    """The cookie carries no last-activity timestamp, so there is nothing for
    the middleware to check -- a session issued once remains valid for
    arbitrarily long idle periods within the same browser session."""
    _create_active_user(db, "idle@rfparchitect.com")
    _login(unauthenticated_client, "idle@rfparchitect.com")

    raw_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)
    payload = _decode_cookie_payload(raw_cookie)
    assert "last_activity_at" not in payload
    assert "created_at" not in payload

    # Many requests later (simulating idle gaps -- the middleware has no
    # clock check to fail this regardless of real elapsed time).
    for _ in range(5):
        assert unauthenticated_client.get("/projects").status_code == 200


def test_weakness_5_no_absolute_timeout_enforced(unauthenticated_client, db):
    """There is no session-creation timestamp anywhere in the cookie or on
    the server, so an absolute session lifetime cannot be enforced: the same
    signed cookie authenticates indefinitely."""
    _create_active_user(db, "absolute@rfparchitect.com")
    _login(unauthenticated_client, "absolute@rfparchitect.com")

    raw_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)
    payload = _decode_cookie_payload(raw_cookie)
    assert "authenticated_at" not in payload
    assert not any(k.endswith("_at") for k in payload)

    # Cookie has no Expires/Max-Age, but is still accepted on replay with no
    # upper bound on session age -- reusing the exact same cookie value.
    resp_a = unauthenticated_client.get("/projects")
    resp_b = unauthenticated_client.get("/projects")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert unauthenticated_client.cookies.get(COOKIE_NAME) == raw_cookie
