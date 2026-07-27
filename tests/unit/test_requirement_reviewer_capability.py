"""Tests for the requirement-reviewer capability guard (A5f Pass 2A).

The capability is the single authority boundary between "may see the project"
and "may approve machine output as authoritative". These tests pin the
fail-closed defaults: no user acquires review authority implicitly.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import (
    REVIEW_AUTH_INACTIVE,
    REVIEW_AUTH_NO_CAPABILITY,
    REVIEW_AUTH_NO_USER,
    REVIEW_AUTH_TENANT_MISMATCH,
    ReviewerAuthorizationError,
    require_requirement_reviewer,
)
from app.models.organization import Organization
from app.models.user import User


def _make_user(db, *, org=None, can_review=False, is_active=True):
    if org is None:
        org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
        db.add(org)
        db.flush()
    user = User(
        organization_id=org.id,
        email=f"u{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        full_name="Reviewer",
        is_active=is_active,
        can_review_requirements=can_review,
    )
    db.add(user)
    db.commit()
    return org, user


def test_capability_defaults_to_false(db):
    """A user created without touching the flag must not be a reviewer."""
    org = Organization(name="DefaultOrg")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"d{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        full_name="Default",
    )
    db.add(user)
    db.commit()

    assert user.can_review_requirements is False


def test_default_user_cannot_review(db):
    org, user = _make_user(db, can_review=False)
    with pytest.raises(ReviewerAuthorizationError) as exc_info:
        require_requirement_reviewer(db, user.id, org.id)
    assert exc_info.value.status_code == 403
    assert exc_info.value.result_code == REVIEW_AUTH_NO_CAPABILITY


def test_capable_same_org_user_can_review(db):
    org, user = _make_user(db, can_review=True)
    authorized = require_requirement_reviewer(db, user.id, org.id)
    assert authorized.id == user.id
    assert authorized.can_review_requirements is True


def test_inactive_capable_user_cannot_review(db):
    org, user = _make_user(db, can_review=True, is_active=False)
    with pytest.raises(ReviewerAuthorizationError) as exc_info:
        require_requirement_reviewer(db, user.id, org.id)
    assert exc_info.value.status_code == 401
    assert exc_info.value.result_code == REVIEW_AUTH_INACTIVE


def test_cross_org_capable_user_gets_non_disclosing_denial(db):
    _org_a, user_a = _make_user(db, can_review=True)
    other_org = Organization(name="OtherOrg")
    db.add(other_org)
    db.commit()

    with pytest.raises(ReviewerAuthorizationError) as exc_info:
        require_requirement_reviewer(db, user_a.id, other_org.id)

    # 404 + generic detail: a cross-tenant caller cannot distinguish
    # "wrong organization" from "lacks capability".
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Not found"
    assert exc_info.value.result_code == REVIEW_AUTH_TENANT_MISMATCH


def test_unknown_user_rejected(db):
    org = Organization(name="EmptyOrg")
    db.add(org)
    db.commit()

    with pytest.raises(ReviewerAuthorizationError) as exc_info:
        require_requirement_reviewer(db, uuid.uuid4(), org.id)
    assert exc_info.value.status_code == 401
    assert exc_info.value.result_code == REVIEW_AUTH_NO_USER


def test_denial_logs_only_fixed_codes_and_ids(db, caplog):
    """Authorization logging must never carry names, emails, or free text."""
    import logging

    org, user = _make_user(db, can_review=False)
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        with pytest.raises(ReviewerAuthorizationError):
            require_requirement_reviewer(db, user.id, org.id)

    assert caplog.records, "expected an authorization denial log record"
    for record in caplog.records:
        message = record.getMessage()
        assert user.email not in message
        assert user.full_name not in message
        assert REVIEW_AUTH_NO_CAPABILITY in message
