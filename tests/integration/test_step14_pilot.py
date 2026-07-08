import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.feedback import PilotFeedback
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.user import User


def test_pilot_documents_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    docs = [
        "PILOT_ONBOARDING_GUIDE.md",
        "PILOT_PARTICIPANT_QUICKSTART.md",
        "PILOT_DATA_HANDLING_NOTICE.md",
        "PILOT_SUCCESS_METRICS.md",
        "PILOT_TRIAGE_WORKFLOW.md",
        "PILOT_EXIT_REPORT_TEMPLATE.md",
    ]

    for doc_name in docs:
        file_path = base_dir / doc_name
        assert file_path.exists(), f"{doc_name} does not exist"

        content = file_path.read_text(encoding="utf-8")
        # Ensure no secrets
        assert "sk-" not in content, f"Possible API key found in {doc_name}"

        # Ensure no fake regulatory claims
        content_lower = content.lower()
        if "gdpr" in content_lower:
            has_gdpr_claim = (
                "compliant" in content_lower and "gdpr compliant" in content_lower
            )
            assert not has_gdpr_claim, (
                f"{doc_name} contains unsupported GDPR compliance claim"
            )
        if "hipaa" in content_lower:
            has_hipaa_claim = (
                "compliant" in content_lower and "hipaa compliant" in content_lower
            )
            assert not has_hipaa_claim, (
                f"{doc_name} contains unsupported HIPAA compliance claim"
            )
        if "soc" in content_lower:
            has_soc_claim = (
                "certified" in content_lower and "soc 2 certified" in content_lower
            )
            assert not has_soc_claim, (
                f"{doc_name} contains unsupported SOC 2 compliance claim"
            )


def test_feedback_requires_auth(unauthenticated_client, monkeypatch):
    from app.core.config import settings

    # Force session mode so dev auth fallback does not fire
    monkeypatch.setattr(settings, "AUTH_MODE", "session")

    # Unauthenticated browser GET redirects to /login
    response = unauthenticated_client.get(
        "/feedback", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # Unauthenticated POST returns 401
    response = unauthenticated_client.post(
        "/feedback",
        data={"category": "BUG", "severity": "LOW", "message": "test"},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_feedback_csrf_enforcement(client, db):
    # Get default org and user
    _, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)

    # Authenticate the client session via POST /login
    login_resp = client.post(
        "/login", data={"email": user.email}, follow_redirects=False
    )
    assert login_resp.status_code == 303

    # Make post with CSRF enforcement but without token (should fail with 403)
    response = client.post(
        "/feedback",
        data={
            "category": "BUG",
            "severity": "LOW",
            "message": "Valid message text",
        },
        headers={"x-test-enforce-csrf": "true"},
    )
    assert response.status_code in (403, 400)


def test_feedback_scoping_and_validation(client, db):
    # Get default org and user
    _, user1_id = get_default_org_and_user(db)
    user1 = db.get(User, user1_id)

    # Authenticate client as user1 (Org 1) via POST /login first
    # to avoid org order issues when logging in after creating org2.
    login_resp = client.post(
        "/login", data={"email": user1.email}, follow_redirects=False
    )
    assert login_resp.status_code == 303

    # Create a second organization and a project belonging to it
    org2 = Organization(id=uuid.uuid4(), name="Org 2")
    db.add(org2)
    db.commit()

    project2 = ProposalProject(
        id=uuid.uuid4(),
        organization_id=org2.id,
        name="Foreign Project",
        client_name="Foreign Client",
        created_by_id=user1_id,
    )
    db.add(project2)
    db.commit()

    # 1. Invalid category
    response = client.post(
        "/feedback",
        data={
            "category": "INVALID",
            "severity": "LOW",
            "message": "Valid message",
        },
    )
    assert response.status_code == 400
    assert "Invalid category" in response.text

    # 2. Invalid message length (whitespace message)
    response = client.post(
        "/feedback",
        data={
            "category": "BUG",
            "severity": "LOW",
            "message": "   ",
        },
    )
    assert response.status_code == 400

    # 3. Project belonging to another org (Org 2)
    response = client.post(
        "/feedback",
        data={
            "project_id": str(project2.id),
            "category": "BUG",
            "severity": "LOW",
            "message": "Valid message",
        },
    )
    assert response.status_code == 404
    assert "Project not found" in response.text


def test_feedback_success_creates_record(client, db):
    # Get default org and user
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)

    # Create project belonging to default org
    project = ProposalProject(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Pilot Project",
        client_name="Pilot Client",
        created_by_id=user_id,
    )
    db.add(project)
    db.commit()

    # Authenticate client via POST /login
    login_resp = client.post(
        "/login", data={"email": user.email}, follow_redirects=False
    )
    assert login_resp.status_code == 303

    response = client.post(
        "/feedback",
        data={
            "project_id": str(project.id),
            "category": "USABILITY",
            "severity": "MEDIUM",
            "message": "This is a great pilot rehearsal!",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert f"/projects/{project.id}" in response.headers["location"]

    # Verify database record
    record = db.scalar(
        select(PilotFeedback).where(PilotFeedback.project_id == project.id)
    )
    assert record is not None
    assert record.category == "USABILITY"
    assert record.severity == "MEDIUM"
    assert record.message == "This is a great pilot rehearsal!"
    assert record.status == "OPEN"
    assert record.organization_id == org_id
    assert record.created_by_user_id == user_id
