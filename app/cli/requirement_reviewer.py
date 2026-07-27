"""Operator CLI for granting and revoking the requirement-reviewer capability.

    python -m app.cli.requirement_reviewer grant \
        --user-email alice@example.com \
        --organization-id 8a3d2f1e-... \
        --reason "Approved in CHG-1421" \
        --confirm

    python -m app.cli.requirement_reviewer revoke \
        --user-email alice@example.com \
        --organization-id 8a3d2f1e-... \
        --reason "Left the bid team" \
        --confirm

What this is
------------
An **operator-controlled bootstrap mechanism**. It relies on the caller already
having authorized database and runtime access, and it exists so the first
reviewer in an organization can be provisioned at all.

What this is not
----------------
It is not a self-service administration interface, not an admin portal, and not
an identity-governance system. There is deliberately **no web route** that
grants this capability -- in particular, no user can grant it to themselves
through the application, which is the whole point of keeping provisioning out
of band.

Safety properties
-----------------
- Deny by default: a write happens only with an explicit ``--confirm``.
- Exact match only: the user must exist *and* belong to the named organization.
  A user in a different organization is refused rather than moved.
- An explicit, non-empty ``--reason`` is required and recorded.
- Idempotent: re-running a grant or revoke is a no-op that reports the settled
  state.
- Never creates users, never changes organization membership, never touches any
  other field, and never grants a generic administrator role.
- Output is limited to the target user id, organization id, result and
  timestamp. No password hash, session, secret, email, or unrelated user data
  is printed.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.audit import AuditEvent
from app.models.organization import Organization
from app.models.user import User

AUDIT_CAPABILITY_GRANTED = "capability_granted"
AUDIT_CAPABILITY_REVOKED = "capability_revoked"

CAPABILITY = "can_review_requirements"

# Fixed result codes.
RESULT_GRANTED = "GRANTED"
RESULT_REVOKED = "REVOKED"
RESULT_ALREADY_GRANTED = "ALREADY_GRANTED"
RESULT_ALREADY_REVOKED = "ALREADY_REVOKED"
RESULT_DRY_RUN = "DRY_RUN_NO_CHANGE_WRITTEN"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3


class CapabilityCommandError(Exception):
    """Operator-facing failure. Messages never contain user data."""


def _resolve_user(db: Session, email: str, organization_id: uuid.UUID) -> User:
    """Find the user, requiring an exact organization match.

    A user who exists but belongs to a different organization is refused
    outright rather than silently operated on -- provisioning the wrong tenant's
    reviewer is precisely the mistake this guard exists to prevent. The refusal
    message does not distinguish the two cases, so this cannot be used to probe
    which organization an address belongs to.
    """
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise CapabilityCommandError("No such organization.")

    normalized = email.strip().lower()
    matches = list(
        db.scalars(select(User).where(User.organization_id == organization_id))
    )
    scoped = [u for u in matches if u.email.strip().lower() == normalized]

    if len(scoped) > 1:
        # Should be impossible (email is unique) but refuse rather than guess.
        raise CapabilityCommandError(
            "Multiple users match in this organization; refusing to guess."
        )
    if not scoped:
        raise CapabilityCommandError(
            "No matching user in that organization. "
            "This command never creates users or moves them between "
            "organizations."
        )
    return scoped[0]


def _write_audit(
    db: Session,
    user: User,
    organization_id: uuid.UUID,
    action: str,
    reason: str,
    result: str,
) -> None:
    db.add(
        AuditEvent(
            organization_id=organization_id,
            user_id=None,  # An operator action, not an in-app user action.
            action=action,
            entity_type="User",
            entity_id=user.id,
            details={
                "capability": CAPABILITY,
                "target_user_id": str(user.id),
                "organization_id": str(organization_id),
                "reason": reason,
                "result": result,
                "source": "operator_cli",
            },
        )
    )


def set_capability(
    db: Session,
    email: str,
    organization_id: uuid.UUID,
    grant: bool,
    reason: str,
    confirm: bool,
) -> tuple[User, str]:
    """Apply (or preview) a capability change. Returns (user, result_code)."""
    if not reason or not reason.strip():
        raise CapabilityCommandError("A non-empty --reason is required.")

    user = _resolve_user(db, email, organization_id)
    current = bool(user.can_review_requirements)

    if current == grant:
        # Idempotent: report the settled state, write nothing, audit nothing.
        return user, (RESULT_ALREADY_GRANTED if grant else RESULT_ALREADY_REVOKED)

    if not confirm:
        # Deny by default. Nothing is written without an explicit --confirm.
        return user, RESULT_DRY_RUN

    user.can_review_requirements = grant
    result = RESULT_GRANTED if grant else RESULT_REVOKED
    _write_audit(
        db,
        user,
        organization_id,
        AUDIT_CAPABILITY_GRANTED if grant else AUDIT_CAPABILITY_REVOKED,
        reason.strip(),
        result,
    )
    db.commit()
    return user, result


def _report(user: User, organization_id: uuid.UUID, result: str) -> None:
    """Print the minimum an operator needs. No user data beyond the id."""
    print(f"capability      : {CAPABILITY}")
    print(f"target_user_id  : {user.id}")
    print(f"organization_id : {organization_id}")
    print(f"result          : {result}")
    print(f"timestamp       : {datetime.now(UTC).isoformat()}")
    if result == RESULT_DRY_RUN:
        print("note            : re-run with --confirm to apply this change")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.requirement_reviewer",
        description=(
            "Grant or revoke the requirement-reviewer capability. "
            "Operator bootstrap tool; requires authorized database access."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, helptext in (
        ("grant", "Grant can_review_requirements to one user."),
        ("revoke", "Revoke can_review_requirements from one user."),
    ):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("--user-email", required=True)
        cmd.add_argument("--organization-id", required=True)
        cmd.add_argument(
            "--reason", required=True, help="Change reason, recorded in the audit log."
        )
        cmd.add_argument(
            "--confirm",
            action="store_true",
            help="Required to write. Without it the command reports a dry run.",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        organization_id = uuid.UUID(args.organization_id)
    except ValueError:
        print("error: --organization-id must be a UUID", file=sys.stderr)
        return EXIT_USAGE

    db = SessionLocal()
    try:
        user, result = set_capability(
            db,
            email=args.user_email,
            organization_id=organization_id,
            grant=args.command == "grant",
            reason=args.reason,
            confirm=args.confirm,
        )
    except CapabilityCommandError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_NOT_FOUND
    finally:
        db.close()

    _report(user, organization_id, result)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
