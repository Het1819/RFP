"""add document_pages provenance fields and parse metadata columns

Revision ID: c5a1e2d3f4b5
Revises: b82741d7293a
Create Date: 2026-07-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a1e2d3f4b5"
down_revision: str | Sequence[str] | None = "b82741d7293a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Add parse metadata columns to documents
    op.add_column(
        "documents",
        sa.Column("parse_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "parse_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("parse_attempt_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("parse_error_code", sa.String(length=100), nullable=True),
    )

    # Add provenance fields to document_pages if table exists
    if inspector.has_table("document_pages"):
        op.add_column(
            "document_pages",
            sa.Column("unit_kind", sa.String(length=50), nullable=True),
        )
        op.add_column(
            "document_pages",
            sa.Column("source_locator", sa.String(length=255), nullable=True),
        )
        op.add_column(
            "document_pages",
            sa.Column("content_sha256", sa.String(length=64), nullable=True),
        )

        # Add unique constraint on (document_id, page_number)
        try:
            op.create_unique_constraint(
                "uq_document_pages_doc_id_page_num",
                "document_pages",
                ["document_id", "page_number"],
            )
        except Exception:
            # SQLite batch mode fallback
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("document_pages"):
        try:
            op.drop_constraint(
                "uq_document_pages_doc_id_page_num",
                "document_pages",
                type_="unique",
            )
        except Exception:
            pass

        op.drop_column("document_pages", "content_sha256")
        op.drop_column("document_pages", "source_locator")
        op.drop_column("document_pages", "unit_kind")

    op.drop_column("documents", "parse_error_code")
    op.drop_column("documents", "parse_attempt_id")
    op.drop_column("documents", "parse_attempt_count")
    op.drop_column("documents", "parse_started_at")
