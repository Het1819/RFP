import re

import pytest
from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.document import Document
from app.models.project import ProposalProject
from app.models.requirement import Requirement


def extract_csrf_token(html_content: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', html_content)
    if not match:
        match = re.search(r'value="([a-f0-9]+)"\s+name="csrf_token"', html_content)
    if not match:
        raise ValueError("CSRF token not found in HTML")
    return match.group(1)


def test_production_app_env_missing_session_secret():
    """Proves config validation fails in production if session secret is weak."""
    from app.core.config import Settings

    # Missing session secret (None) in production environment should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        Settings(APP_ENV="production", AUTH_MODE="session", SESSION_SECRET_KEY=None)
    assert "SESSION_SECRET_KEY must be set" in str(exc_info.value)

    # Weak session secret in production environment should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production", AUTH_MODE="session", SESSION_SECRET_KEY="short-key"
        )
    assert "SESSION_SECRET_KEY must be set" in str(exc_info.value)


def test_csrf_rendered_on_get_pages(client):
    """Proves that GET pages successfully generate and render a CSRF token."""
    response = client.get("/projects")
    assert response.status_code == 200
    token = extract_csrf_token(response.text)
    assert len(token) == 64  # secrets.token_hex(32) produces 64 character hex string


def test_post_without_csrf_token_fails(client):
    """Proves that mutating POST requests without a CSRF token are blocked with 403."""
    response = client.post(
        "/projects",
        data={"name": "Attacker Project", "client_name": "Target Client"},
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert response.status_code == 403
    assert "CSRF token validation failed" in response.text


def test_post_with_invalid_csrf_token_fails(client):
    """Proves mutating POST with invalid token fails with 403."""
    # Seed session by hitting a GET route first
    client.get("/projects")

    response = client.post(
        "/projects",
        data={
            "name": "Attacker Project",
            "client_name": "Target Client",
            "csrf_token": "wrongtoken" * 8,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert response.status_code == 403
    assert "CSRF token validation failed" in response.text


def test_mutating_actions_with_valid_csrf_token_succeeds(client, db, monkeypatch):
    """Proves that state-mutating actions pass when a valid CSRF token is provided."""
    # A5c Task 6: reaching SCANNING now calls enqueue_scan_job(), which
    # with QUEUE_ENABLED=false (test default) would run a real scan
    # attempt synchronously in-process (including a real ClamAV socket
    # connection). Stub it - this test is about CSRF, not the scanner.
    import app.core.queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_scan_job", lambda document_id: None)

    # 1. Access project creation and get token
    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    # 2. POST to create project with token
    payload = {
        "name": "Acme Project Alpha",
        "client_name": "Acme",
        "csrf_token": csrf_token,
    }
    post_resp = client.post(
        "/projects",
        data=payload,
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert post_resp.status_code == 303

    # Verify project was created
    proj = db.scalars(
        select(ProposalProject).where(ProposalProject.name == "Acme Project Alpha")
    ).first()
    assert proj is not None

    # 3. Get Project Detail to render RFP upload token
    detail_resp = client.get(f"/projects/{proj.id}")
    assert detail_resp.status_code == 200
    detail_token = extract_csrf_token(detail_resp.text)

    # 4. Attempt upload with valid token
    from tests.integration.test_projects import create_test_pdf

    pdf_content = create_test_pdf(["Requirement: System must scale."])
    upload_file = {"file": ("rfp.pdf", pdf_content, "application/pdf")}
    upload_resp = client.post(
        f"/projects/{proj.id}/upload",
        data={"csrf_token": detail_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        files=upload_file,
        follow_redirects=False,
    )
    assert upload_resp.status_code == 303

    # Wait / retrieve document
    doc = db.scalars(select(Document).where(Document.project_id == proj.id)).first()
    assert doc is not None

    # 5. Retrieve compliance matrix page to get merge token
    matrix_resp = client.get(f"/projects/{proj.id}/matrix")
    assert matrix_resp.status_code == 200
    matrix_token = extract_csrf_token(matrix_resp.text)

    # Add requirements directly to database
    req1 = Requirement(project_id=proj.id, original_text="Req scale 1")
    req2 = Requirement(project_id=proj.id, original_text="Req scale 2")
    db.add(req1)
    db.add(req2)
    db.commit()

    # 6. POST to merge requirements
    merge_resp = client.post(
        f"/projects/{proj.id}/matrix/merge",
        data={"ids": [str(req1.id), str(req2.id)], "csrf_token": matrix_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert merge_resp.status_code == 303


def test_htmx_header_csrf_token_path_succeeds(client, db):
    """Proves that HTMX requests using X-CSRF-Token headers succeed."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="HTMX Proj",
        client_name="HTMX",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Editable Requirement")
    db.add(req)
    db.commit()

    # 1. Seed session and get token
    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    # 2. Perform HTMX POST using header
    edit_payload = {
        "original_text": "Updated text",
        "status": "NOT_STARTED",
    }
    headers = {"X-CSRF-Token": csrf_token, "X-Test-Enforce-CSRF": "true"}

    response = client.post(
        f"/requirements/{req.id}/edit",
        headers=headers,
        data=edit_payload,
        follow_redirects=False,
    )
    assert response.status_code == 200

    db.refresh(req)
    assert req.original_text == "Updated text"
