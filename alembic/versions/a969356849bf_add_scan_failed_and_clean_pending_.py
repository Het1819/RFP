"""add SCAN_FAILED and CLEAN_PENDING_PROMOTION ingestion statuses

Revision ID: a969356849bf
Revises: 04ffd9fbcedb
Create Date: 2026-07-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a969356849bf"
down_revision: str | Sequence[str] | None = "04ffd9fbcedb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_INGESTION_STATUSES = (
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

_NEW_INGESTION_STATUSES = (
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


def upgrade() -> None:
    """Upgrade schema.

    A5c introduces a new intermediate accept state,
    CLEAN_PENDING_PROMOTION (documents that pass malware scanning and
    content-policy inspection stop here in this phase - promotion to
    CLEAN is deferred to A5d), and SCAN_FAILED, which replaces the
    never-actually-written SCAN_RETRY_PENDING scaffolded by A5a. The
    ingestion_status CHECK constraint is dropped and recreated against
    the new status list; ingestion_state.py's ALLOWED_TRANSITIONS is the
    single sanctioned mutator of this column and now routes
    SCANNING -> CLEAN_PENDING_PROMOTION instead of SCANNING -> CLEAN.

    Also adds scan_started_at / scan_attempt_count /
    content_policy_version / scan_digest_snapshot metadata columns used
    by the scan worker (later tasks in this phase) to detect and fail
    closed on a document whose quarantine file or digest changed between
    enqueue and scan execution.

    Uses op.batch_alter_table() rather than bare op.drop_constraint() /
    op.create_check_constraint(): SQLite has no native ALTER TABLE
    DROP/ADD CONSTRAINT support (confirmed empirically -
    alembic.ddl.sqlite raises NotImplementedError for a bare
    drop_constraint() call), so a CHECK-constraint change must go
    through Alembic's batch mode (copy-and-move table recreation) to be
    portable across SQLite and PostgreSQL. On PostgreSQL, batch mode
    transparently emits plain ALTER TABLE statements - no table
    recreation - so behavior there is unchanged.
    """
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_ingestion_status_valid", type_="check")
        batch_op.add_column(
            sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "scan_attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("content_policy_version", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("scan_digest_snapshot", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_documents_ingestion_status_valid",
            sa.column("ingestion_status").in_(_NEW_INGESTION_STATUSES),
        )


def downgrade() -> None:
    """Downgrade schema.

    Schema reversal only, not a data migration: this assumes no row has
    ingestion_status in {SCAN_FAILED, CLEAN_PENDING_PROMOTION} at
    downgrade time (nothing in this codebase writes those values on any
    branch older than this revision, since they did not exist). If that
    assumption doesn't hold, restoring the old CHECK constraint will
    fail loudly rather than silently corrupt data - fix the offending
    rows before downgrading.
    """
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_ingestion_status_valid", type_="check")
        batch_op.drop_column("scan_digest_snapshot")
        batch_op.drop_column("content_policy_version")
        batch_op.drop_column("scan_attempt_count")
        batch_op.drop_column("scan_started_at")
        batch_op.create_check_constraint(
            "ck_documents_ingestion_status_valid",
            sa.column("ingestion_status").in_(_OLD_INGESTION_STATUSES),
        )
