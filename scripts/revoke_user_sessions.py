# scripts/revoke_user_sessions.py
# Administrative tool to immediately revoke every active session for one
# existing user (e.g. suspected credential compromise, offboarding).
#
# Usage:
#   uv run python scripts/revoke_user_sessions.py user@example.com
#   uv run python scripts/revoke_user_sessions.py user@example.com --yes
#
# Looks up the user by case-insensitive exact email match. Never creates
# users. Requires interactive confirmation unless --yes is given. Never
# prints raw session identifiers -- only the account and a session count.
# Do not run this against production from an unreviewed workstation.

import argparse
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _revoke(email: str, *, assume_yes: bool) -> int:
    from sqlalchemy import func, select

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.core.sessions.store import RedisSessionStore, SessionStoreUnavailableError
    from app.models.user import User

    normalized_email = email.strip().lower()
    if not normalized_email:
        print("ERROR: email must not be empty.")
        return 1

    db = SessionLocal()
    try:
        matches = db.scalars(
            select(User).where(func.lower(User.email) == normalized_email)
        ).all()

        if not matches:
            print(f"ERROR: no user found with email '{normalized_email}'.")
            return 1
        if len(matches) > 1:
            print(f"ERROR: email '{normalized_email}' matches multiple users.")
            return 1

        user = matches[0]

        store = RedisSessionStore(settings.effective_session_redis_url)
        try:
            pending = await store.count_for_user(str(user.id))
        except SessionStoreUnavailableError as exc:
            print(f"ERROR: session store unavailable: {exc}")
            return 1

        print(f"Account: {normalized_email} (user_id={user.id})")
        print(f"Sessions to revoke: {pending}")

        if not assume_yes:
            answer = input("Revoke ALL sessions for this account? [y/N]: ").strip()
            if answer.lower() not in ("y", "yes"):
                print("Aborted; no sessions were revoked.")
                return 1

        try:
            revoked_count = await store.revoke_all_for_user(str(user.id))
        except SessionStoreUnavailableError as exc:
            print(f"ERROR: session store unavailable: {exc}")
            return 1

        print(f"Revoked {revoked_count} session(s) for '{normalized_email}'.")

        try:
            from app.services.project_service import log_audit_event

            log_audit_event(
                db,
                org_id=user.organization_id,
                user_id=user.id,
                action="ADMIN_SESSION_REVOCATION",
                entity_type="User",
                entity_id=user.id,
                details={"revoked_count": revoked_count},
            )
            db.commit()
        except Exception as exc:
            # Best-effort audit trail: revocation already happened and must
            # not be reported as failed just because the audit write failed.
            print(f"WARNING: revocation succeeded but audit logging failed: {exc}")

        from app.core.observability import MetricsRegistry

        MetricsRegistry.session_admin_revocations += 1
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revoke every active session for one existing user."
    )
    parser.add_argument("email", help="Exact account email (case-insensitive).")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args()

    return asyncio.run(_revoke(args.email, assume_yes=args.yes))


if __name__ == "__main__":
    sys.exit(main())
