"""add document ingestion security metadata

Revision ID: 04ffd9fbcedb
Revises: 7a14e99f1390
Create Date: 2026-07-25 13:33:16.838454

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04ffd9fbcedb"
down_revision: str | Sequence[str] | None = "7a14e99f1390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INGESTION_STATUSES = (
    "QUARANTINED",
    "VALIDATING",
    "SCANNING",
    "SCAN_RETRY_PENDING",
    "REJECTED_TYPE",
    "REJECTED_MALWARE",
    "REJECTED_CONTENT_POLICY",
    "CLEAN",
    "PARSING",
    "PARSE_FAILED",
    "COMPLETED",
    "LEGACY_UNVERIFIED",
)


def upgrade() -> None:
    """Upgrade schema.

    Adds ingestion-security metadata to documents. ingestion_status is
    added NOT NULL with server_default='LEGACY_UNVERIFIED' so every
    pre-existing row is explicitly marked as not-yet-security-verified
    (never silently marked CLEAN/COMPLETED) - see A5 spec section 4.
    """
    op.add_column(
        "documents",
        sa.Column(
            "ingestion_status",
            sa.String(length=30),
            nullable=False,
            server_default="LEGACY_UNVERIFIED",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("display_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("detected_content_type", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("sha256_digest", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("scan_status", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("scan_engine_version", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("scan_signature_version", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("scan_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("content_policy_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("parser_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("parser_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("rejection_reason_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents", sa.Column("operator_failure_summary", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_documents_ingestion_status_valid",
        "documents",
        sa.column("ingestion_status").in_(_INGESTION_STATUSES),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_documents_ingestion_status_valid", "documents", type_="check"
    )
    op.drop_column("documents", "operator_failure_summary")
    op.drop_column("documents", "rejection_reason_code")
    op.drop_column("documents", "parser_completed_at")
    op.drop_column("documents", "parser_version")
    op.drop_column("documents", "content_policy_status")
    op.drop_column("documents", "scan_completed_at")
    op.drop_column("documents", "scan_signature_version")
    op.drop_column("documents", "scan_engine_version")
    op.drop_column("documents", "scan_status")
    op.drop_column("documents", "quarantined_at")
    op.drop_column("documents", "file_size_bytes")
    op.drop_column("documents", "sha256_digest")
    op.drop_column("documents", "detected_content_type")
    op.drop_column("documents", "display_filename")
    op.drop_column("documents", "ingestion_status")
