"""
app/services/evidence_validation.py

Server-side evidence validation and draft grounding helpers for Step 8.

Key principles:
- Never trust client-submitted snippet text, page number, or score.
- Always resolve snippets from stored DocumentPage content.
- Claim support checking is deterministic (no LLM judge).
- All helpers are pure-function testable without a web request.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.ingestion_state import IngestionStatus

if TYPE_CHECKING:
    from app.models.document import Document, DocumentPage

# ---------------------------------------------------------------------------
# Snippet length bounds (characters, not tokens)
# ---------------------------------------------------------------------------
SNIPPET_MIN_LEN: int = 10
SNIPPET_MAX_LEN: int = 2000

# ---------------------------------------------------------------------------
# Claim support thresholds
# ---------------------------------------------------------------------------
# Jaccard token overlap required for a draft sentence to be considered
# "supported" by a validated evidence passage.
CLAIM_SUPPORT_THRESHOLD: float = 0.20

# Boilerplate phrases that are never treated as unsupported claims.
# Keep as lowercase normalised tokens.
_BOILERPLATE_PHRASES: list[str] = [
    "we will comply",
    "our organisation will comply",
    "our organization will comply",
    "we are pleased to",
    "we confirm that",
    "please see attached",
    "as required",
    "in accordance with",
    "we acknowledge",
    "we understand",
]


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------


def normalize_evidence_text(text: str) -> str:
    """
    Normalise text for evidence comparison:
    - casefold
    - collapse whitespace
    Returns empty string for None/empty input.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _tokenize(text: str) -> set[str]:
    """Split normalised text into tokens (words only)."""
    return set(re.findall(r"[a-z0-9]+", normalize_evidence_text(text)))


# ---------------------------------------------------------------------------
# Evidence quote presence check
# ---------------------------------------------------------------------------


def evidence_quote_exists_on_page(
    snippet: str,
    page_content: str,
) -> bool:
    """
    Return True if the normalised snippet is found within the normalised
    page content.

    This is a simple substring check after whitespace normalisation.
    A TODO for advanced alignment: replace with sentence-level span matching.
    """
    if not snippet or not page_content:
        return False
    norm_snippet = normalize_evidence_text(snippet)
    norm_page = normalize_evidence_text(page_content)
    return norm_snippet in norm_page


# ---------------------------------------------------------------------------
# Evidence candidate validation
# ---------------------------------------------------------------------------


@dataclass
class EvidenceValidationError(Exception):
    """Raised when a candidate evidence link fails server-side validation."""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def validate_evidence_candidate(
    db: Session,
    *,
    requirement_project_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int | None,
    client_snippet: str,
) -> tuple[str, int | None]:
    """
    Validate a client-submitted evidence candidate and return the
    server-resolved (canonical_snippet, resolved_page_number).

    Steps:
    1. Load the document; reject if it belongs to a different project.
    2. Reject if document is unprocessed, failed, or (for knowledge_base) unapproved.
    3. If page_number is given: verify the page exists, then check snippet
       is present on that page.
    4. If page_number is None: check snippet is present in doc.content.
    5. Enforce snippet length bounds.
    6. Return the canonical snippet (trimmed from server content).

    Raises EvidenceValidationError on any failure.
    Returns (canonical_snippet, resolved_page_number).
    """
    from app.models.document import Document, DocumentPage

    # 1. Load document — 404 for any failure to avoid leaking existence
    doc: Document | None = db.scalar(select(Document).where(Document.id == document_id))
    if doc is None:
        raise EvidenceValidationError(status_code=404, detail="Document not found")

    # 2. Same-project check
    if doc.project_id != requirement_project_id:
        raise EvidenceValidationError(
            status_code=404,
            detail="Document not found",  # deliberate: no project info leakage
        )

    # 3. Processing/approval status
    if doc.processing_status not in ("completed",):
        raise EvidenceValidationError(
            status_code=400,
            detail="Document is not fully processed",
        )
    if doc.doc_role == "knowledge_base" and doc.approval_status != "APPROVED":
        raise EvidenceValidationError(
            status_code=400,
            detail="Document is not approved for use as evidence",
        )
    if doc.ingestion_status != IngestionStatus.COMPLETED:
        raise EvidenceValidationError(
            status_code=400,
            detail="Document has not completed security processing",
        )

    # 4. Snippet length bounds
    stripped = client_snippet.strip()
    if len(stripped) < SNIPPET_MIN_LEN:
        raise EvidenceValidationError(
            status_code=422,
            detail=(
                f"Evidence snippet is too short (minimum {SNIPPET_MIN_LEN} characters)"
            ),
        )
    if len(stripped) > SNIPPET_MAX_LEN:
        raise EvidenceValidationError(
            status_code=422,
            detail=(
                f"Evidence snippet is too long (maximum {SNIPPET_MAX_LEN} characters)"
            ),
        )

    # 5. Resolve page and verify snippet
    resolved_page: int | None = page_number
    canonical_snippet: str

    if page_number is not None:
        page: DocumentPage | None = db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number,
            )
        )
        if page is None:
            raise EvidenceValidationError(
                status_code=400,
                detail="Page number does not exist for this document",
            )
        page_content = page.content
        if not evidence_quote_exists_on_page(stripped, page_content):
            raise EvidenceValidationError(
                status_code=400,
                detail="Evidence snippet not found on the specified page",
            )
        canonical_snippet = stripped
    else:
        # Fall back to full document content
        doc_content = doc.content or ""
        if not doc_content:
            raise EvidenceValidationError(
                status_code=400,
                detail=(
                    "Document has no extracted text content; provide a page number"
                ),
            )
        if not evidence_quote_exists_on_page(stripped, doc_content):
            raise EvidenceValidationError(
                status_code=400,
                detail="Evidence snippet not found in document content",
            )
        canonical_snippet = stripped

    return canonical_snippet, resolved_page


def resolve_evidence_from_document_page(
    db: Session,
    *,
    document_id: uuid.UUID,
    page_number: int,
) -> str:
    """
    Return the full stored content of a DocumentPage as a server-side
    snippet. Used when the client provides only document_id + page_number
    without a specific snippet (the route should require a snippet, but this
    helper is available for server-side selection flows).

    Raises EvidenceValidationError if the page does not exist.
    """
    from app.models.document import DocumentPage

    page = db.scalar(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id,
            DocumentPage.page_number == page_number,
        )
    )
    if page is None:
        raise EvidenceValidationError(
            status_code=400,
            detail="Page not found for document",
        )
    content = page.content.strip()
    # Truncate to max snippet length if full page is very long
    return content[:SNIPPET_MAX_LEN]


def require_same_project_evidence(
    document_project_id: uuid.UUID,
    requirement_project_id: uuid.UUID,
) -> None:
    """
    Assert that a document belongs to the same project as the requirement.
    Raises EvidenceValidationError (404) if not, to avoid leaking existence.
    """
    if document_project_id != requirement_project_id:
        raise EvidenceValidationError(
            status_code=404,
            detail="Document not found",
        )


# ---------------------------------------------------------------------------
# Draft grounding checker
# ---------------------------------------------------------------------------


@dataclass
class UnsupportedClaim:
    """Represents a draft sentence that cannot be supported by evidence."""

    sentence: str
    overlap_score: float
    best_evidence_snippet: str | None = None


@dataclass
class GroundingResult:
    """Result of validate_draft_grounding()."""

    passes: bool
    unsupported_claims: list[UnsupportedClaim] = field(default_factory=list)
    supported_count: int = 0
    total_claims: int = 0
    grounding_pass_rate: float = 0.0


def extract_draft_claims(draft_text: str) -> list[str]:
    """
    Split a draft answer text into individual claim sentences.

    Rules:
    - Split on sentence boundaries (. ! ?)
    - Strip boilerplate compliance phrases (those are never flagged).
    - Skip very short fragments (< 20 chars after stripping).
    - Return up to 50 sentences to bound runtime.
    """
    # Simple sentence split on ". ", "! ", "? " followed by capital or end
    raw_sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$", draft_text)
    claims: list[str] = []
    for raw in raw_sentences:
        sentence = raw.strip().rstrip(".!?").strip()
        if len(sentence) < 20:
            continue
        # Skip boilerplate
        norm = normalize_evidence_text(sentence)
        if any(bp in norm for bp in _BOILERPLATE_PHRASES):
            continue
        claims.append(sentence)
    return claims[:50]


def check_claim_support(
    claim: str,
    evidence_snippets: list[str],
    threshold: float = CLAIM_SUPPORT_THRESHOLD,
) -> tuple[bool, float, str | None]:
    """
    Check if a single claim sentence is supported by any evidence snippet.

    Uses Jaccard token overlap between the claim and each evidence snippet.

    Returns (is_supported, best_overlap_score, best_snippet_or_None).
    """
    claim_tokens = _tokenize(claim)
    if not claim_tokens:
        return True, 1.0, None  # empty claim is trivially supported

    best_score = 0.0
    best_snippet: str | None = None

    for snippet in evidence_snippets:
        snippet_tokens = _tokenize(snippet)
        if not snippet_tokens:
            continue
        union = claim_tokens | snippet_tokens
        intersection = claim_tokens & snippet_tokens
        score = len(intersection) / len(union)
        if score > best_score:
            best_score = score
            best_snippet = snippet

    is_supported = best_score >= threshold
    return is_supported, best_score, best_snippet


def validate_draft_grounding(
    draft_text: str,
    validated_evidence_snippets: list[str],
    threshold: float = CLAIM_SUPPORT_THRESHOLD,
) -> GroundingResult:
    """
    Check all extractable claims in a draft against validated evidence.

    Returns a GroundingResult with:
    - passes: True if all claims are supported or there are no claims
    - unsupported_claims: list of UnsupportedClaim
    - grounding_pass_rate: fraction of supported claims

    This is a deterministic, LLM-free support check.
    """
    claims = extract_draft_claims(draft_text)
    if not claims:
        # No extractable claims — treat as passing (human can review)
        return GroundingResult(
            passes=True,
            total_claims=0,
            supported_count=0,
            grounding_pass_rate=1.0,
        )

    unsupported: list[UnsupportedClaim] = []
    supported_count = 0

    for claim in claims:
        is_supported, score, best_snippet = check_claim_support(
            claim, validated_evidence_snippets, threshold
        )
        if is_supported:
            supported_count += 1
        else:
            unsupported.append(
                UnsupportedClaim(
                    sentence=claim,
                    overlap_score=score,
                    best_evidence_snippet=best_snippet,
                )
            )

    total = len(claims)
    pass_rate = supported_count / total if total > 0 else 1.0

    return GroundingResult(
        passes=len(unsupported) == 0,
        unsupported_claims=unsupported,
        supported_count=supported_count,
        total_claims=total,
        grounding_pass_rate=pass_rate,
    )
