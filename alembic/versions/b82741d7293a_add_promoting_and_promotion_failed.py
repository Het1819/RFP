"""add PROMOTING and PROMOTION_FAILED ingestion statuses and promotion metadata columns

Revision ID: b82741d7293a
Revises: a969356849bf
Create Date: 2026-07-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b82741d7293a"
down_revision: str | Sequence[str] | None = "a969356849bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_INGESTION_STATUSES = (
    "QUARANTINED",
    "VALIDATING",
    "SCANNING",
    "REJECTED_TYPE",
    "REJECTED_MALWARE",
    "REJECTED_CONTENT_POLICY",
    "SCAN_FAILED",
    "CLEAN_PENDING_PROMOTION",
    "CLEAN",
    "PARSING",
    "PARSE_FAILED",
    "COMPLETED",
    "LEGACY_UNVERIFIED",
)

_NEW_INGESTION_STATUSES = (
    "QUARANTINED",
    "VALIDATING",
    "SCANNING",
    "REJECTED_TYPE",
    "REJECTED_MALWARE",
    "REJECTED_CONTENT_POLICY",
    "SCAN_FAILED",
    "CLEAN_PENDING_PROMOTION",
    "PROMOTING",
    "PROMOTION_FAILED",
    "CLEAN",
    "PARSING",
    "PARSE_FAILED",
    "COMPLETED",
    "LEGACY_UNVERIFIED",
)


def upgrade() -> None:
    """Upgrade schema.

    A5d introduces PROMOTING and PROMOTION_FAILED ingestion statuses, plus
    promotion metadata columns (promotion_started_at, promotion_completed_at,
    promotion_attempt_count, clean_storage_identifier, cleanup_pending).
    """
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_ingestion_status_valid", type_="check")
        batch_op.add_column(
            sa.Column("promotion_started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "promotion_completed_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "promotion_attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("clean_storage_identifier", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "cleanup_pending",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            )
        )
        batch_op.create_check_constraint(
            "ck_documents_ingestion_status_valid",
            sa.column("ingestion_status").in_(_NEW_INGESTION_STATUSES),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_ingestion_status_valid", type_="check")
        batch_op.drop_column("cleanup_pending")
        batch_op.drop_column("clean_storage_identifier")
        batch_op.drop_column("promotion_attempt_count")
        batch_op.drop_column("promotion_completed_at")
        batch_op.drop_column("promotion_started_at")
        batch_op.create_check_constraint(
            "ck_documents_ingestion_status_valid",
            sa.column("ingestion_status").in_(_OLD_INGESTION_STATUSES),
        )
