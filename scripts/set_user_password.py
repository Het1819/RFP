# scripts/set_user_password.py
# Administrative tool to set/reset an existing user's password.
#
# Usage:
#   python scripts/set_user_password.py <user-email>
#
# The password is entered interactively (getpass) and never accepted as a
# command-line argument or printed. This script only updates an existing,
# unambiguous user; it never creates users.

import os
import sys
from getpass import getpass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_PASSWORD_LENGTH = 15


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/set_user_password.py <user-email>")
        return 1

    email = sys.argv[1].strip().lower()
    if not email:
        print("ERROR: email must not be empty.")
        return 1

    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.core.passwords import hash_password
    from app.models.user import User

    db = SessionLocal()
    try:
        matches = db.scalars(select(User).where(func.lower(User.email) == email)).all()

        if not matches:
            print(f"ERROR: no user found with email '{email}'.")
            return 1
        if len(matches) > 1:
            print(f"ERROR: email '{email}' matches multiple users; ambiguous.")
            return 1

        user = matches[0]

        password = getpass("New password: ")
        if not password:
            print("ERROR: password must not be empty.")
            return 1
        if len(password) < MIN_PASSWORD_LENGTH:
            print(
                f"ERROR: password must be at least {MIN_PASSWORD_LENGTH} "
                "characters long."
            )
            return 1

        confirm = getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: passwords do not match.")
            return 1

        user.hashed_password = hash_password(password)
        db.commit()

        print(f"Password updated successfully for '{email}'.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
