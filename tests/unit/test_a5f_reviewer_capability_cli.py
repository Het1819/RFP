"""Operator CLI tests for reviewer-capability provisioning (A5f Pass 2B2).

This command is the only way the capability is granted, so its guardrails are
the guardrails. Every test runs against the test database session.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.cli.requirement_reviewer import (
    AUDIT_CAPABILITY_GRANTED,
    AUDIT_CAPABILITY_REVOKED,
    RESULT_ALREADY_GRANTED,
    RESULT_ALREADY_REVOKED,
    RESULT_DRY_RUN,
    RESULT_GRANTED,
    RESULT_REVOKED,
    CapabilityCommandError,
    _report,
    build_parser,
    set_capability,
)
from app.models.audit import AuditEvent
from app.models.organization import Organization
from app.models.user import User

REASON = "Approved in CHG-1421"


def _seed(db, *, can_review=False):
    org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="argon2-hash-do-not-print",
        full_name="Reviewer Person",
        can_review_requirements=can_review,
    )
    db.add(user)
    db.commit()
    return org, user


def _audits(db, action):
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ---------------------------------------------------------------------------
# Defaults and happy paths
# ---------------------------------------------------------------------------


def test_capability_defaults_false(db):
    _org, user = _seed(db)
    assert user.can_review_requirements is False


def test_grant_sets_capability(db):
    org, user = _seed(db)
    result_user, result = set_capability(
        db, user.email, org.id, grant=True, reason=REASON, confirm=True
    )
    assert result == RESULT_GRANTED
    assert result_user.id == user.id
    db.refresh(user)
    assert user.can_review_requirements is True


def test_repeated_grant_is_idempotent(db):
    org, user = _seed(db)
    set_capability(db, user.email, org.id, grant=True, reason=REASON, confirm=True)
    _u, second = set_capability(
        db, user.email, org.id, grant=True, reason=REASON, confirm=True
    )
    assert second == RESULT_ALREADY_GRANTED
    db.refresh(user)
    assert user.can_review_requirements is True
    # A no-op writes no second audit row.
    assert len(_audits(db, AUDIT_CAPABILITY_GRANTED)) == 1


def test_revoke_clears_capability(db):
    org, user = _seed(db, can_review=True)
    _u, result = set_capability(
        db, user.email, org.id, grant=False, reason=REASON, confirm=True
    )
    assert result == RESULT_REVOKED
    db.refresh(user)
    assert user.can_review_requirements is False


def test_repeated_revoke_is_idempotent(db):
    org, user = _seed(db, can_review=True)
    set_capability(db, user.email, org.id, grant=False, reason=REASON, confirm=True)
    _u, second = set_capability(
        db, user.email, org.id, grant=False, reason=REASON, confirm=True
    )
    assert second == RESULT_ALREADY_REVOKED
    assert len(_audits(db, AUDIT_CAPABILITY_REVOKED)) == 1


def test_email_match_is_case_insensitive_and_trimmed(db):
    org, user = _seed(db)
    _u, result = set_capability(
        db, f"  {user.email.upper()}  ", org.id, grant=True, reason=REASON, confirm=True
    )
    assert result == RESULT_GRANTED


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_wrong_organization_is_refused(db):
    _org, user = _seed(db)
    other = Organization(name="OtherOrg")
    db.add(other)
    db.commit()

    with pytest.raises(CapabilityCommandError):
        set_capability(
            db, user.email, other.id, grant=True, reason=REASON, confirm=True
        )

    db.refresh(user)
    # The user is neither granted nor moved between organizations.
    assert user.can_review_requirements is False
    assert user.organization_id != other.id


def test_unknown_user_is_refused_and_creates_nobody(db):
    org, _user = _seed(db)
    before = len(db.scalars(select(User)).all())

    with pytest.raises(CapabilityCommandError):
        set_capability(
            db, "nobody@example.com", org.id, grant=True, reason=REASON, confirm=True
        )

    assert len(db.scalars(select(User)).all()) == before


def test_unknown_organization_is_refused(db):
    _org, user = _seed(db)
    with pytest.raises(CapabilityCommandError):
        set_capability(
            db, user.email, uuid.uuid4(), grant=True, reason=REASON, confirm=True
        )


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_missing_reason_is_refused(db, reason):
    org, user = _seed(db)
    with pytest.raises(CapabilityCommandError):
        set_capability(db, user.email, org.id, grant=True, reason=reason, confirm=True)
    db.refresh(user)
    assert user.can_review_requirements is False


def test_missing_confirmation_writes_nothing(db):
    org, user = _seed(db)
    _u, result = set_capability(
        db, user.email, org.id, grant=True, reason=REASON, confirm=False
    )
    assert result == RESULT_DRY_RUN
    db.refresh(user)
    assert user.can_review_requirements is False
    assert _audits(db, AUDIT_CAPABILITY_GRANTED) == []


# ---------------------------------------------------------------------------
# Audit and blast radius
# ---------------------------------------------------------------------------


def test_grant_emits_audit_event_with_reason(db):
    org, user = _seed(db)
    set_capability(db, user.email, org.id, grant=True, reason=REASON, confirm=True)

    events = _audits(db, AUDIT_CAPABILITY_GRANTED)
    assert len(events) == 1
    details = events[0].details
    assert details["capability"] == "can_review_requirements"
    assert details["target_user_id"] == str(user.id)
    assert details["reason"] == REASON
    assert details["result"] == RESULT_GRANTED
    assert details["source"] == "operator_cli"
    assert events[0].entity_id == user.id
    assert events[0].organization_id == org.id
    # Never records a hash or an in-app actor for an operator action.
    assert "argon2-hash-do-not-print" not in str(details)
    assert events[0].user_id is None


def test_revoke_emits_audit_event(db):
    org, user = _seed(db, can_review=True)
    set_capability(db, user.email, org.id, grant=False, reason=REASON, confirm=True)
    assert len(_audits(db, AUDIT_CAPABILITY_REVOKED)) == 1


def test_no_other_user_fields_are_modified(db):
    org, user = _seed(db)
    before = {
        "email": user.email,
        "hashed_password": user.hashed_password,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "organization_id": user.organization_id,
        "created_at": user.created_at,
    }

    set_capability(db, user.email, org.id, grant=True, reason=REASON, confirm=True)

    db.refresh(user)
    for field, value in before.items():
        assert getattr(user, field) == value, f"{field} was modified"
    assert user.can_review_requirements is True


def test_other_users_are_untouched(db):
    org, user = _seed(db)
    bystander = User(
        organization_id=org.id,
        email=f"b{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        full_name="Bystander",
    )
    db.add(bystander)
    db.commit()

    set_capability(db, user.email, org.id, grant=True, reason=REASON, confirm=True)

    db.refresh(bystander)
    assert bystander.can_review_requirements is False


def test_report_prints_no_secret_or_user_data(db, capsys):
    org, user = _seed(db)
    _report(user, org.id, RESULT_GRANTED)
    out = capsys.readouterr().out

    assert str(user.id) in out
    assert str(org.id) in out
    assert RESULT_GRANTED in out
    # No hash, email, name, or session material.
    assert "argon2-hash-do-not-print" not in out
    assert user.email not in out
    assert "Reviewer Person" not in out


# ---------------------------------------------------------------------------
# Argument surface
# ---------------------------------------------------------------------------


def test_parser_requires_reason_and_offers_confirm():
    parser = build_parser()
    args = parser.parse_args(
        [
            "grant",
            "--user-email",
            "a@b.com",
            "--organization-id",
            str(uuid.uuid4()),
            "--reason",
            "because",
            "--confirm",
        ]
    )
    assert args.command == "grant"
    assert args.confirm is True

    # Missing --reason is rejected by the parser itself.
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["grant", "--user-email", "a@b.com", "--organization-id", str(uuid.uuid4())]
        )


def test_confirm_defaults_to_false():
    parser = build_parser()
    args = parser.parse_args(
        [
            "revoke",
            "--user-email",
            "a@b.com",
            "--organization-id",
            str(uuid.uuid4()),
            "--reason",
            "because",
        ]
    )
    assert args.confirm is False


def test_no_self_grant_web_route_exists():
    """The capability must not be grantable through the application."""
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        assert "can_review" not in path
        assert "capability" not in path.lower()
