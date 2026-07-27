"""add extraction_runs, requirement_candidates, candidate_review_tasks

Revision ID: d1e2f3a4b5c6
Revises: c5a1e2d3f4b5
Create Date: 2026-07-27

A5f Pass 1: requirement-candidate extraction foundation.

Tables added:
  extraction_runs           -- one per document/snapshot/schema/prompt attempt
  requirement_candidates    -- PROPOSED candidates from extraction runs
  candidate_review_tasks    -- one human-review task per candidate

Idempotency index:
  A partial unique index on extraction_runs prevents re-running a completed
  extraction for the same (document_id, input_snapshot_sha256,
  extraction_schema_version, prompt_version).  SQLite does not support partial
  indexes so the guard is skipped in SQLite environments (tests use
  application-layer idempotency checks instead).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c5a1e2d3f4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ------------------------------------------------------------------
    # extraction_runs
    # ------------------------------------------------------------------
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("extraction_attempt_id", sa.String(36), nullable=False),
        sa.Column("input_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=True),
        sa.Column("extraction_schema_version", sa.String(50), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_extraction_runs_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["proposal_projects.id"],
            ondelete="CASCADE",
            name="fk_extraction_runs_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_extraction_runs_document_id",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED')",
            name="ck_extraction_runs_status",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0",
            name="ck_extraction_runs_candidate_count_nonneg",
        ),
        sa.CheckConstraint(
            "page_count >= 0",
            name="ck_extraction_runs_page_count_nonneg",
        ),
    )
    op.create_index(
        "ix_extraction_runs_organization_id", "extraction_runs", ["organization_id"]
    )
    op.create_index("ix_extraction_runs_project_id", "extraction_runs", ["project_id"])
    op.create_index(
        "ix_extraction_runs_document_id", "extraction_runs", ["document_id"]
    )

    # Partial unique index to prevent duplicate successful runs.
    # Only supported on PostgreSQL; SQLite tests use application-layer guard.
    if not is_sqlite:
        op.execute(
            """
            CREATE UNIQUE INDEX uq_extraction_runs_completed_snapshot
            ON extraction_runs (
                document_id,
                input_snapshot_sha256,
                extraction_schema_version,
                prompt_version
            )
            WHERE status = 'COMPLETED'
            """
        )

    # ------------------------------------------------------------------
    # requirement_candidates
    # ------------------------------------------------------------------
    op.create_table(
        "requirement_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("extraction_run_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_page_id", sa.UUID(), nullable=False),
        sa.Column("page_content_sha256", sa.String(64), nullable=False),
        sa.Column("unit_kind", sa.String(50), nullable=False),
        sa.Column("source_locator", sa.String(255), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("uncertainty_reason", sa.String(500), nullable=True),
        sa.Column("extraction_schema_version", sa.String(50), nullable=False),
        sa.Column(
            "candidate_status", sa.String(20), nullable=False, server_default="PROPOSED"
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewer_comment", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_candidates"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_requirement_candidates_org_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["proposal_projects.id"],
            ondelete="CASCADE",
            name="fk_requirement_candidates_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            ondelete="CASCADE",
            name="fk_requirement_candidates_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_requirement_candidates_doc_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_page_id"],
            ["document_pages.id"],
            ondelete="CASCADE",
            name="fk_requirement_candidates_page_id",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_requirement_candidates_reviewed_by",
        ),
        sa.CheckConstraint(
            "candidate_status IN "
            "('PROPOSED','APPROVED','EDITED','REJECTED','SUPERSEDED')",
            name="ck_requirement_candidates_status",
        ),
        sa.CheckConstraint(
            "span_start >= 0",
            name="ck_requirement_candidates_span_start_nonneg",
        ),
        sa.CheckConstraint(
            "span_end > span_start",
            name="ck_requirement_candidates_span_end_gt_start",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL) OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_requirement_candidates_confidence_range",
        ),
    )
    op.create_index(
        "ix_requirement_candidates_org_id",
        "requirement_candidates",
        ["organization_id"],
    )
    op.create_index(
        "ix_requirement_candidates_project_id", "requirement_candidates", ["project_id"]
    )
    op.create_index(
        "ix_requirement_candidates_run_id",
        "requirement_candidates",
        ["extraction_run_id"],
    )
    op.create_index(
        "ix_requirement_candidates_doc_id", "requirement_candidates", ["document_id"]
    )
    op.create_index(
        "ix_requirement_candidates_page_id",
        "requirement_candidates",
        ["document_page_id"],
    )

    # ------------------------------------------------------------------
    # candidate_review_tasks
    # ------------------------------------------------------------------
    op.create_table(
        "candidate_review_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("extraction_run_id", sa.UUID(), nullable=False),
        sa.Column(
            "task_type",
            sa.String(50),
            nullable=False,
            server_default="REQUIREMENT_CANDIDATE_REVIEW",
        ),
        sa.Column("source_locator", sa.String(255), nullable=False),
        sa.Column("assigned_to_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_review_tasks"),
        sa.UniqueConstraint(
            "candidate_id", name="uq_candidate_review_tasks_candidate_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_candidate_review_tasks_org_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["proposal_projects.id"],
            ondelete="CASCADE",
            name="fk_candidate_review_tasks_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["requirement_candidates.id"],
            ondelete="CASCADE",
            name="fk_candidate_review_tasks_candidate_id",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            ondelete="CASCADE",
            name="fk_candidate_review_tasks_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_candidate_review_tasks_assigned_to",
        ),
        sa.CheckConstraint(
            "task_type = 'REQUIREMENT_CANDIDATE_REVIEW'",
            name="ck_candidate_review_tasks_type",
        ),
    )
    op.create_index(
        "ix_candidate_review_tasks_org_id",
        "candidate_review_tasks",
        ["organization_id"],
    )
    op.create_index(
        "ix_candidate_review_tasks_project_id", "candidate_review_tasks", ["project_id"]
    )
    op.create_index(
        "ix_candidate_review_tasks_candidate_id",
        "candidate_review_tasks",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_review_tasks_run_id",
        "candidate_review_tasks",
        ["extraction_run_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_table("candidate_review_tasks")
    op.drop_table("requirement_candidates")

    if not is_sqlite:
        op.execute("DROP INDEX IF EXISTS uq_extraction_runs_completed_snapshot")

    op.drop_table("extraction_runs")
