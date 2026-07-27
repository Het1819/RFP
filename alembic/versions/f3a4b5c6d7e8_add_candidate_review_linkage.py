"""add candidate review linkage, reviewer text, and extraction run counters

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-27

A5f Pass 2A:

  requirements.source_candidate_id
      Provenance link from an authoritative Requirement back to the reviewed
      candidate it was promoted from. Nullable for legacy rows; UNIQUE so the
      database -- not service discipline -- guarantees at most one Requirement
      per candidate, which is what makes duplicate/replayed approval safe.
      ondelete=RESTRICT: an approved candidate is the provenance record for a
      live Requirement and must not vanish underneath it.

  requirement_candidates.reviewer_edited_text
      Reviewer-authored replacement text for an EDITED decision, stored beside
      (never over) the original machine proposal.

  extraction_runs.{received,accepted,skipped}_candidate_count
  extraction_runs.validation_issue_counts
      Per-run validation accounting, now that a run can complete with some
      candidates skipped. Counts and fixed reason codes only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_COUNTS_TYPE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("source_candidate_id", sa.UUID(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_requirements_source_candidate_id",
        "requirements",
        ["source_candidate_id"],
    )
    op.create_index(
        "ix_requirements_source_candidate_id",
        "requirements",
        ["source_candidate_id"],
    )
    op.create_foreign_key(
        "fk_requirements_source_candidate_id",
        "requirements",
        "requirement_candidates",
        ["source_candidate_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "requirement_candidates",
        sa.Column("reviewer_edited_text", sa.Text(), nullable=True),
    )

    for column in (
        "received_candidate_count",
        "accepted_candidate_count",
        "skipped_candidate_count",
    ):
        op.add_column(
            "extraction_runs",
            sa.Column(column, sa.Integer(), server_default="0", nullable=False),
        )
    op.add_column(
        "extraction_runs",
        sa.Column("validation_issue_counts", _JSON_COUNTS_TYPE, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_runs", "validation_issue_counts")
    for column in (
        "skipped_candidate_count",
        "accepted_candidate_count",
        "received_candidate_count",
    ):
        op.drop_column("extraction_runs", column)

    op.drop_column("requirement_candidates", "reviewer_edited_text")

    op.drop_constraint(
        "fk_requirements_source_candidate_id", "requirements", type_="foreignkey"
    )
    op.drop_index("ix_requirements_source_candidate_id", table_name="requirements")
    op.drop_constraint(
        "uq_requirements_source_candidate_id", "requirements", type_="unique"
    )
    op.drop_column("requirements", "source_candidate_id")
