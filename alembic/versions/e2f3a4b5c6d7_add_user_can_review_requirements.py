"""add users.can_review_requirements reviewer capability

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-27

A5f Pass 2A: the narrow, organization-scoped capability that authorizes a human
to review extracted requirement candidates and promote them to authoritative
Requirements.

The column is NOT NULL with a server default of false, so every pre-existing
row is backfilled to false by the ALTER itself. No user is auto-granted the
capability by this migration -- granting is an explicit, separate action.

The server default is retained (not dropped after backfill) because it is the
intended steady-state default for newly inserted rows: a user must never
acquire review authority implicitly.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_review_requirements",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_review_requirements")
