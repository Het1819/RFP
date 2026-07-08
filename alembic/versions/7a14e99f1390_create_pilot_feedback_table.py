"""create_pilot_feedback_table

Revision ID: 7a14e99f1390
Revises: ce1d99d00aeb
Create Date: 2026-07-08 16:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a14e99f1390"
down_revision: str | Sequence[str] | None = "ce1d99d00aeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pilot_feedbacks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("requirement_id", sa.UUID(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["proposal_projects.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pilot_feedbacks_created_by_user_id"),
        "pilot_feedbacks",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pilot_feedbacks_organization_id"),
        "pilot_feedbacks",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pilot_feedbacks_project_id"),
        "pilot_feedbacks",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pilot_feedbacks_requirement_id"),
        "pilot_feedbacks",
        ["requirement_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_pilot_feedbacks_requirement_id"),
        table_name="pilot_feedbacks",
    )
    op.drop_index(op.f("ix_pilot_feedbacks_project_id"), table_name="pilot_feedbacks")
    op.drop_index(
        op.f("ix_pilot_feedbacks_organization_id"),
        table_name="pilot_feedbacks",
    )
    op.drop_index(
        op.f("ix_pilot_feedbacks_created_by_user_id"),
        table_name="pilot_feedbacks",
    )
    op.drop_table("pilot_feedbacks")
