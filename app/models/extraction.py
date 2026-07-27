"""ORM models for requirement-extraction runs and candidate records (A5f).

Design decisions
----------------
- Existing ``Requirement`` model cannot safely represent candidates: it has no
  extraction_run_id, no candidate_status, no span provenance, no evidence
  SHA-256 binding, and no immutability guard.  Adding those fields would break
  existing compliance-matrix queries and conflate authoritative records with
  proposed ones.  ``RequirementCandidate`` is therefore a separate model.

- Existing ``ReviewTask`` is hardwired to ``requirement_id`` FK.  A separate
  ``CandidateReviewTask`` model links to ``RequirementCandidate`` without
  modifying the existing review flow.

- ``ExtractionRun`` prevents duplicate successful runs for the same
  (document_id, input_snapshot_sha256, extraction_schema_version,
  prompt_version) via a partial unique index defined in the migration.

- Span offsets are Unicode code-point offsets into ``DocumentPage.content``.

- ``evidence_sha256`` binds the stored evidence_text to the page content slice
  captured at extraction time.

- No candidate state transition is automatic.  PROPOSED -> APPROVED/EDITED/
  REJECTED/SUPERSEDED requires an authenticated human action (A5f Pass 2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Portable JSON column, mirroring app.models.audit: JSONB on PostgreSQL,
# generic JSON on SQLite so create_all() works in the test suite.
_JSON_COUNTS_TYPE = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXTRACTION_STATUS_PENDING = "PENDING"
EXTRACTION_STATUS_RUNNING = "RUNNING"
EXTRACTION_STATUS_COMPLETED = "COMPLETED"
EXTRACTION_STATUS_FAILED = "FAILED"

EXTRACTION_STATUSES = frozenset(
    {
        EXTRACTION_STATUS_PENDING,
        EXTRACTION_STATUS_RUNNING,
        EXTRACTION_STATUS_COMPLETED,
        EXTRACTION_STATUS_FAILED,
    }
)

CANDIDATE_STATUS_PROPOSED = "PROPOSED"
CANDIDATE_STATUS_APPROVED = "APPROVED"
CANDIDATE_STATUS_EDITED = "EDITED"
CANDIDATE_STATUS_REJECTED = "REJECTED"
CANDIDATE_STATUS_SUPERSEDED = "SUPERSEDED"

CANDIDATE_STATUSES = frozenset(
    {
        CANDIDATE_STATUS_PROPOSED,
        CANDIDATE_STATUS_APPROVED,
        CANDIDATE_STATUS_EDITED,
        CANDIDATE_STATUS_REJECTED,
        CANDIDATE_STATUS_SUPERSEDED,
    }
)

CANDIDATE_REVIEW_TASK_TYPE = "REQUIREMENT_CANDIDATE_REVIEW"

REVIEW_TASK_STATUS_OPEN = "OPEN"
REVIEW_TASK_STATUS_COMPLETED = "COMPLETED"
REVIEW_TASK_STATUS_SUPERSEDED = "SUPERSEDED"

# Fixed audit actions for the candidate review lifecycle. Payloads carry IDs,
# versions, decisions, and fixed result codes only -- never source text,
# evidence text, model output, secrets, or raw exception detail.
AUDIT_CANDIDATE_APPROVED = "candidate_review_approved"
AUDIT_CANDIDATE_EDITED = "candidate_review_edited"
AUDIT_CANDIDATE_REJECTED = "candidate_review_rejected"
AUDIT_CANDIDATE_CONFLICT = "candidate_review_conflict"
AUDIT_CANDIDATE_SUPERSEDED = "candidate_superseded"
AUDIT_CANDIDATE_UNAUTHORIZED = "candidate_review_unauthorized"

# Extraction lifecycle audit actions (A5f Pass 2B1).
AUDIT_EXTRACTION_QUEUED = "extraction_queued"
AUDIT_EXTRACTION_STARTED = "extraction_started"
AUDIT_EXTRACTION_COMPLETED = "extraction_completed"
AUDIT_EXTRACTION_FAILED = "extraction_failed"
AUDIT_EXTRACTION_RETRY_SCHEDULED = "extraction_retry_scheduled"
AUDIT_EXTRACTION_INPUT_LIMIT = "extraction_input_limit_exceeded"

# Reviewer-supplied text for an EDITED decision.
MAX_REVIEWER_EDITED_TEXT_LEN = 2000
MAX_REVIEWER_COMMENT_LEN = 1000

# Hard bounds enforced in validation layer and CheckConstraints.
MAX_REQUIREMENT_TEXT_LEN = 2000
MAX_EVIDENCE_TEXT_LEN = 4000


# ---------------------------------------------------------------------------
# ExtractionRun
# ---------------------------------------------------------------------------


class ExtractionRun(Base):
    """One extraction attempt for a specific document/snapshot/schema/prompt."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED')",
            name="ck_extraction_runs_status",
        ),
        CheckConstraint(
            "candidate_count >= 0",
            name="ck_extraction_runs_candidate_count_nonneg",
        ),
        CheckConstraint(
            "page_count >= 0",
            name="ck_extraction_runs_page_count_nonneg",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposal_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), default=EXTRACTION_STATUS_PENDING, nullable=False
    )
    extraction_attempt_id: Mapped[str] = mapped_column(
        String(36), nullable=False, default=lambda: str(uuid.uuid4())
    )

    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    extraction_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)

    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Per-run validation accounting. A run can now complete with some
    # candidates skipped, so the reconciliation between what the extractor
    # proposed and what was persisted must be visible without re-running
    # extraction: received == accepted + skipped.
    received_candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    accepted_candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    skipped_candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # {fixed_reason_code: count}. Counts only -- never raw rejected candidate
    # content, which is untrusted model output derived from an untrusted
    # document and must not be persisted or logged.
    validation_issue_counts: Mapped[dict[str, int] | None] = mapped_column(
        _JSON_COUNTS_TYPE, nullable=True
    )

    # Provider usage accounting. Counters only -- the prompt, the source pages,
    # and the model's raw response are never persisted anywhere.
    provider_call_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    cache_creation_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    cache_read_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# RequirementCandidate
# ---------------------------------------------------------------------------


class RequirementCandidate(Base):
    """A proposed requirement extracted from one DocumentPage.

    Span offsets are Unicode code-point offsets into DocumentPage.content.
    evidence_sha256 = SHA-256(evidence_text.encode('utf-8')).
    page_content_sha256 must match DocumentPage.content_sha256 at persistence.
    Only PROPOSED candidates may be created by the extraction service.
    """

    __tablename__ = "requirement_candidates"
    __table_args__ = (
        CheckConstraint(
            "candidate_status IN "
            "('PROPOSED','APPROVED','EDITED','REJECTED','SUPERSEDED')",
            name="ck_requirement_candidates_status",
        ),
        CheckConstraint(
            "span_start >= 0",
            name="ck_requirement_candidates_span_start_nonneg",
        ),
        CheckConstraint(
            "span_end > span_start",
            name="ck_requirement_candidates_span_end_gt_start",
        ),
        CheckConstraint(
            "(confidence IS NULL) OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_requirement_candidates_confidence_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposal_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    page_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)

    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    normalized_requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Reviewer-authored replacement text for an EDITED decision. Stored
    # separately so normalized_requirement_text always remains the untouched
    # machine proposal -- an audit trail of what the model said versus what the
    # human decided it should say. Unlike evidence_text this is NOT required to
    # be a source slice: a reviewer may legitimately rewrite a requirement.
    reviewer_edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirement_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    uncertainty_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    extraction_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)

    candidate_status: Mapped[str] = mapped_column(
        String(20), default=CANDIDATE_STATUS_PROPOSED, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# CandidateReviewTask
# ---------------------------------------------------------------------------


class CandidateReviewTask(Base):
    """One human-review task linked to exactly one RequirementCandidate.

    Created atomically with the candidate.  Contains only safe identifiers --
    no document content is stored in task metadata.
    """

    __tablename__ = "candidate_review_tasks"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_candidate_review_tasks_candidate_id"),
        CheckConstraint(
            "task_type = 'REQUIREMENT_CANDIDATE_REVIEW'",
            name="ck_candidate_review_tasks_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposal_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_candidates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_type: Mapped[str] = mapped_column(
        String(50),
        default=CANDIDATE_REVIEW_TASK_TYPE,
        nullable=False,
    )
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)

    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
