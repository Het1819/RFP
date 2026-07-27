"""Human review of requirement candidates and promotion to Requirements (A5f).

This module is the authority boundary of the product. Everything upstream of it
is machine output over untrusted document text; everything downstream of it is
an authoritative Requirement that appears in the compliance matrix and can be
exported to a customer. The only thing that moves a row across that line is an
explicit decision by an authenticated human holding
``User.can_review_requirements``.

Transitions
-----------
    PROPOSED -> APPROVED   creates one Requirement from the candidate text
    PROPOSED -> EDITED     creates one Requirement from the reviewer's text
    PROPOSED -> REJECTED   creates no Requirement

Terminal states never transition again here. SUPERSEDED is reserved for
extraction-run reconciliation (see candidate_extraction) and is never a review
outcome. Nothing transitions on model confidence.

Atomicity
---------
Authorization, locking, source revalidation, the candidate transition, the
Requirement insert, the review-task completion, and the audit event all happen
in one transaction. A failure anywhere leaves the candidate PROPOSED, the task
open, no Requirement, and no audit record of a decision that did not happen.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.observability import request_id_var
from app.core.security import (
    ReviewerAuthorizationError,
    require_requirement_reviewer,
)
from app.models.audit import AuditEvent
from app.models.document import DocumentPage
from app.models.extraction import (
    AUDIT_CANDIDATE_APPROVED,
    AUDIT_CANDIDATE_CONFLICT,
    AUDIT_CANDIDATE_EDITED,
    AUDIT_CANDIDATE_REJECTED,
    AUDIT_CANDIDATE_UNAUTHORIZED,
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_EDITED,
    CANDIDATE_STATUS_PROPOSED,
    CANDIDATE_STATUS_REJECTED,
    MAX_REVIEWER_COMMENT_LEN,
    MAX_REVIEWER_EDITED_TEXT_LEN,
    REVIEW_TASK_STATUS_COMPLETED,
    REVIEW_TASK_STATUS_OPEN,
    CandidateReviewTask,
    ExtractionRun,
    RequirementCandidate,
)
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.user import User
from app.services.extraction_contract import find_unsafe_content

logger = logging.getLogger(__name__)

DECISION_APPROVE = "APPROVE"
DECISION_EDIT = "EDIT"
DECISION_REJECT = "REJECT"

VALID_DECISIONS = frozenset({DECISION_APPROVE, DECISION_EDIT, DECISION_REJECT})

_DECISION_TO_STATUS = {
    DECISION_APPROVE: CANDIDATE_STATUS_APPROVED,
    DECISION_EDIT: CANDIDATE_STATUS_EDITED,
    DECISION_REJECT: CANDIDATE_STATUS_REJECTED,
}

_DECISION_TO_AUDIT = {
    DECISION_APPROVE: AUDIT_CANDIDATE_APPROVED,
    DECISION_EDIT: AUDIT_CANDIDATE_EDITED,
    DECISION_REJECT: AUDIT_CANDIDATE_REJECTED,
}

# Fixed result codes. Safe to log, audit, and surface as a generic error.
REVIEW_OK = "REVIEW_OK"
REVIEW_NOT_FOUND = "REVIEW_CANDIDATE_NOT_FOUND"
REVIEW_INVALID_DECISION = "REVIEW_INVALID_DECISION"
REVIEW_ALREADY_DECIDED = "REVIEW_ALREADY_DECIDED"
REVIEW_SOURCE_DRIFT = "REVIEW_SOURCE_DRIFT"
REVIEW_RUN_NOT_APPLICABLE = "REVIEW_RUN_NOT_APPLICABLE"
REVIEW_EDIT_TEXT_REQUIRED = "REVIEW_EDIT_TEXT_REQUIRED"
REVIEW_EDIT_TEXT_INVALID = "REVIEW_EDIT_TEXT_INVALID"
REVIEW_COMMENT_INVALID = "REVIEW_COMMENT_INVALID"
REVIEW_TASK_MISSING = "REVIEW_TASK_MISSING"
REVIEW_CONFLICT = "REVIEW_CONFLICT"


class CandidateReviewError(Exception):
    """Terminal failure of a review action, carrying a fixed result code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ReviewResult:
    """Outcome of a completed review action."""

    def __init__(
        self,
        candidate_id: uuid.UUID,
        candidate_status: str,
        requirement_id: uuid.UUID | None,
        result_code: str,
        idempotent_replay: bool = False,
    ) -> None:
        self.candidate_id = candidate_id
        self.candidate_status = candidate_status
        self.requirement_id = requirement_id
        self.result_code = result_code
        self.idempotent_replay = idempotent_replay


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_reviewer_text(raw: str) -> str:
    """Normalize and validate reviewer-authored requirement text.

    Reviewer text is deliberately NOT required to match the source evidence:
    the whole point of an EDITED decision is that a human may rewrite what the
    model proposed. The evidence slice remains immutable and independently
    visible, so the reviewer's words never overwrite the record of what the
    document actually said.

    URLs and instruction-shaped contractual language are permitted -- a real
    requirement may well say "register at https://portal.example.gov". They are
    stored as inert text: never fetched, never rendered as markup (templates
    autoescape), never fed back to a model as instructions.

    Raises CandidateReviewError for empty, oversized, or non-text input.
    """
    # NFC keeps visually identical text byte-identical, so a requirement cannot
    # be duplicated or evaded through alternate Unicode encodings.
    text = unicodedata.normalize("NFC", raw).strip()

    if not text:
        raise CandidateReviewError(
            REVIEW_EDIT_TEXT_REQUIRED, "Reviewer text must not be empty"
        )
    if len(text) > MAX_REVIEWER_EDITED_TEXT_LEN:
        raise CandidateReviewError(
            REVIEW_EDIT_TEXT_INVALID,
            f"Reviewer text exceeds {MAX_REVIEWER_EDITED_TEXT_LEN} characters",
        )
    if find_unsafe_content(text) is not None:
        # NUL / control characters only -- not markup, links, or prose shape.
        raise CandidateReviewError(
            REVIEW_EDIT_TEXT_INVALID,
            "Reviewer text contains disallowed control characters",
        )
    return text


def _normalize_comment(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw).strip()
    if not text:
        return None
    if len(text) > MAX_REVIEWER_COMMENT_LEN:
        raise CandidateReviewError(
            REVIEW_COMMENT_INVALID,
            f"Reviewer comment exceeds {MAX_REVIEWER_COMMENT_LEN} characters",
        )
    if find_unsafe_content(text) is not None:
        raise CandidateReviewError(
            REVIEW_COMMENT_INVALID,
            "Reviewer comment contains disallowed control characters",
        )
    return text


def _revalidate_source(db: Session, candidate: RequirementCandidate) -> None:
    """Re-prove the candidate's provenance against live source rows.

    Between extraction and review the document may have been re-parsed, the
    page edited, or the row replaced. Approving under those conditions would
    mint an authoritative Requirement whose cited evidence no longer exists in
    the document, which is exactly the failure this product cannot have.

    Raises CandidateReviewError(REVIEW_SOURCE_DRIFT) if any link in the chain
    no longer holds.
    """
    page = db.get(DocumentPage, candidate.document_page_id)
    if page is None or page.document_id != candidate.document_id:
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Source page is missing or belongs to another document"
        )

    project = db.get(ProposalProject, candidate.project_id)
    if project is None or project.organization_id != candidate.organization_id:
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Candidate project/organization linkage is broken"
        )

    content = page.content

    # Page content must still hash to what was recorded at extraction time.
    if not page.content_sha256 or page.content_sha256 != _sha256_text(content):
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Source page content hash no longer matches"
        )
    if page.content_sha256 != candidate.page_content_sha256:
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Source page changed since extraction"
        )

    # Span must still be addressable.
    if (
        candidate.span_start < 0
        or candidate.span_end > len(content)
        or candidate.span_end <= candidate.span_start
    ):
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Evidence span is no longer within the page"
        )

    # Evidence must still be the exact slice, and hash to the stored digest.
    if content[candidate.span_start : candidate.span_end] != candidate.evidence_text:
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Evidence text no longer matches the source slice"
        )
    if _sha256_text(candidate.evidence_text) != candidate.evidence_sha256:
        raise CandidateReviewError(
            REVIEW_SOURCE_DRIFT, "Evidence hash does not match evidence text"
        )

    # The originating run must still be the applicable one.
    run = db.get(ExtractionRun, candidate.extraction_run_id)
    if (
        run is None
        or run.document_id != candidate.document_id
        or run.organization_id != candidate.organization_id
    ):
        raise CandidateReviewError(
            REVIEW_RUN_NOT_APPLICABLE, "Originating extraction run is not applicable"
        )


def review_requirement_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    org_id: uuid.UUID,
    decision: str,
    edited_text: str | None = None,
    reviewer_comment: str | None = None,
) -> ReviewResult:
    """Apply a human review decision to one requirement candidate.

    Authorization happens before any candidate state is read, so a caller who
    is not a reviewer -- or is in the wrong tenant -- cannot learn whether the
    candidate exists.

    Returns ReviewResult. Raises ReviewerAuthorizationError on authorization
    failure and CandidateReviewError on any other terminal failure, in both
    cases leaving the candidate PROPOSED with no Requirement created.
    """
    if decision not in VALID_DECISIONS:
        raise CandidateReviewError(
            REVIEW_INVALID_DECISION, f"Unsupported review decision: {decision!r}"
        )

    # ------------------------------------------------------------------
    # 1. Authorize BEFORE disclosing candidate state.
    # ------------------------------------------------------------------
    try:
        reviewer: User = require_requirement_reviewer(db, reviewer_id, org_id)
    except ReviewerAuthorizationError as err:
        db.rollback()
        db.add(
            AuditEvent(
                organization_id=org_id,
                user_id=reviewer_id,
                action=AUDIT_CANDIDATE_UNAUTHORIZED,
                entity_type="RequirementCandidate",
                entity_id=candidate_id,
                details={
                    "decision": decision,
                    "result_code": err.result_code,
                },
                request_id=request_id_var.get(),
            )
        )
        db.commit()
        raise

    try:
        # --------------------------------------------------------------
        # 2. Lock the candidate and its review task.
        # --------------------------------------------------------------
        candidate = db.scalar(
            select(RequirementCandidate)
            .where(
                RequirementCandidate.id == candidate_id,
                RequirementCandidate.organization_id == org_id,
            )
            .with_for_update()
        )
        if candidate is None:
            # Same response for "does not exist" and "belongs to another
            # tenant": no cross-tenant existence disclosure.
            raise CandidateReviewError(
                REVIEW_NOT_FOUND, "Requirement candidate not found"
            )

        task = db.scalar(
            select(CandidateReviewTask)
            .where(
                CandidateReviewTask.candidate_id == candidate.id,
                CandidateReviewTask.organization_id == org_id,
            )
            .with_for_update()
        )

        # --------------------------------------------------------------
        # 3. Require PROPOSED. Terminal states never re-transition.
        # --------------------------------------------------------------
        if candidate.candidate_status != CANDIDATE_STATUS_PROPOSED:
            existing = db.scalar(
                select(Requirement).where(
                    Requirement.source_candidate_id == candidate.id
                )
            )
            # A replayed submission of the decision that already won is not an
            # error: report the settled outcome instead of creating a second
            # Requirement or corrupting the first.
            replay_of_same_decision = (
                candidate.candidate_status == _DECISION_TO_STATUS[decision]
            )
            db.add(
                AuditEvent(
                    organization_id=org_id,
                    user_id=reviewer_id,
                    action=AUDIT_CANDIDATE_CONFLICT,
                    entity_type="RequirementCandidate",
                    entity_id=candidate.id,
                    details={
                        "project_id": str(candidate.project_id),
                        "extraction_run_id": str(candidate.extraction_run_id),
                        "decision": decision,
                        "current_status": candidate.candidate_status,
                        "result_code": (
                            REVIEW_ALREADY_DECIDED
                            if replay_of_same_decision
                            else REVIEW_CONFLICT
                        ),
                    },
                    request_id=request_id_var.get(),
                )
            )
            db.commit()

            if replay_of_same_decision:
                return ReviewResult(
                    candidate_id=candidate.id,
                    candidate_status=candidate.candidate_status,
                    requirement_id=existing.id if existing else None,
                    result_code=REVIEW_ALREADY_DECIDED,
                    idempotent_replay=True,
                )
            raise CandidateReviewError(
                REVIEW_CONFLICT,
                f"Candidate is already {candidate.candidate_status}",
            )

        if task is None:
            raise CandidateReviewError(
                REVIEW_TASK_MISSING, "Candidate has no open review task"
            )

        # --------------------------------------------------------------
        # 4-5. Re-read source and revalidate the full provenance chain.
        # --------------------------------------------------------------
        _revalidate_source(db, candidate)

        comment = _normalize_comment(reviewer_comment)

        # --------------------------------------------------------------
        # 6-9. Apply the decision.
        # --------------------------------------------------------------
        now = datetime.now(UTC)
        requirement: Requirement | None = None

        if decision == DECISION_EDIT:
            if edited_text is None:
                raise CandidateReviewError(
                    REVIEW_EDIT_TEXT_REQUIRED,
                    "An EDIT decision requires reviewer text",
                )
            reviewed_text = normalize_reviewer_text(edited_text)
            # The machine proposal in normalized_requirement_text is preserved
            # untouched; the reviewer's wording is stored beside it.
            candidate.reviewer_edited_text = reviewed_text
            requirement_text = reviewed_text
        elif decision == DECISION_APPROVE:
            requirement_text = candidate.normalized_requirement_text
        else:
            requirement_text = ""

        candidate.candidate_status = _DECISION_TO_STATUS[decision]
        candidate.reviewed_at = now
        candidate.reviewed_by = reviewer.id
        candidate.reviewer_comment = comment

        if decision in (DECISION_APPROVE, DECISION_EDIT):
            requirement = Requirement(
                project_id=candidate.project_id,
                source_document_id=candidate.document_id,
                source_candidate_id=candidate.id,
                original_text=requirement_text,
                source_page=None,
                source_section=candidate.source_locator,
                requirement_type=candidate.requirement_type,
                status="NOT_STARTED",
            )
            db.add(requirement)
            db.flush()  # surface the unique violation here, inside the txn

        # --------------------------------------------------------------
        # 10. Complete the review task.
        # --------------------------------------------------------------
        if task.status == REVIEW_TASK_STATUS_OPEN:
            task.status = REVIEW_TASK_STATUS_COMPLETED
            task.assigned_to_id = task.assigned_to_id or reviewer.id
            task.resolved_at = now

        # --------------------------------------------------------------
        # 11. Audit, in the same transaction as the decision itself.
        # --------------------------------------------------------------
        db.add(
            AuditEvent(
                organization_id=org_id,
                user_id=reviewer.id,
                action=_DECISION_TO_AUDIT[decision],
                entity_type="RequirementCandidate",
                entity_id=candidate.id,
                details={
                    "project_id": str(candidate.project_id),
                    "document_id": str(candidate.document_id),
                    "extraction_run_id": str(candidate.extraction_run_id),
                    "requirement_id": str(requirement.id) if requirement else None,
                    "reviewer_id": str(reviewer.id),
                    "decision": decision,
                    "candidate_status": candidate.candidate_status,
                    "extraction_schema_version": candidate.extraction_schema_version,
                    "reviewed_at": now.isoformat(),
                    "result_code": REVIEW_OK,
                },
                request_id=request_id_var.get(),
            )
        )

        # --------------------------------------------------------------
        # 12. Commit atomically.
        # --------------------------------------------------------------
        db.commit()

    except CandidateReviewError:
        db.rollback()
        raise
    except IntegrityError as err:
        # The unique constraint on Requirement.source_candidate_id is the last
        # line of defence against two concurrent reviewers both promoting the
        # same candidate. Losing that race is a conflict, not a crash.
        db.rollback()
        logger.warning(
            "candidate_review.integrity_conflict: candidate_id=%s org_id=%s",
            candidate_id,
            org_id,
        )
        raise CandidateReviewError(
            REVIEW_CONFLICT, "Candidate was reviewed concurrently"
        ) from err
    except Exception as err:
        db.rollback()
        logger.error(
            "candidate_review.failed: candidate_id=%s org_id=%s type=%s",
            candidate_id,
            org_id,
            type(err).__name__,
        )
        raise CandidateReviewError(
            REVIEW_CONFLICT, "Review could not be completed"
        ) from err

    logger.info(
        "candidate_review.decided: candidate_id=%s org_id=%s decision=%s "
        "requirement_id=%s",
        candidate.id,
        org_id,
        decision,
        requirement.id if requirement else None,
    )

    return ReviewResult(
        candidate_id=candidate.id,
        candidate_status=candidate.candidate_status,
        requirement_id=requirement.id if requirement else None,
        result_code=REVIEW_OK,
    )
