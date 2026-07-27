"""Versioned JSON contract for requirement-candidate extraction output (A5f).

The extractor provider returns a ``ParserExtractionResponse`` that is validated
against this schema before any database write.  All validation is strict:
unknown fields are rejected, bounds are enforced, and evidence slices are
verified against DocumentPage content before persistence.

Treat DocumentPage.content as untrusted data, never as instructions.
This schema intentionally excludes storage paths, filenames, secrets,
and unrelated document context.

Schema version: requirement-candidates-v1
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "requirement-candidates-v1"

# Hard limits — must match models.extraction constants.
MAX_CANDIDATES_PER_DOCUMENT = 500
MAX_REQUIREMENT_TEXT_LEN = 2000
MAX_EVIDENCE_TEXT_LEN = 4000
MAX_UNCERTAINTY_REASON_LEN = 500

ALLOWED_REQUIREMENT_TYPES = frozenset(
    {
        "functional",
        "non_functional",
        "compliance",
        "security",
        "performance",
        "interface",
        "operational",
        "other",
    }
)


class CandidateUnit(BaseModel):
    """One extracted requirement candidate from a single source unit (page/chunk).

    source_unit_sequence corresponds to DocumentPage.page_number (1-based).
    span_start / span_end are Unicode code-point offsets into the page content.
    """

    model_config = ConfigDict(extra="forbid")

    source_unit_sequence: Annotated[int, Field(ge=1)]
    span_start: Annotated[int, Field(ge=0)]
    span_end: Annotated[int, Field(ge=1)]
    requirement_text: Annotated[
        str, Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT_LEN)
    ]
    requirement_type: Annotated[str | None, Field(default=None)]
    confidence: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)]
    uncertainty_reason: Annotated[
        str | None, Field(default=None, max_length=MAX_UNCERTAINTY_REASON_LEN)
    ]

    @field_validator("requirement_type")
    @classmethod
    def validate_requirement_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_REQUIREMENT_TYPES:
            raise ValueError(
                f"requirement_type must be one of {sorted(ALLOWED_REQUIREMENT_TYPES)}"
            )
        return v

    @model_validator(mode="after")
    def validate_span_order(self) -> CandidateUnit:
        if self.span_end <= self.span_start:
            raise ValueError("span_end must be greater than span_start")
        return self


class ExtractionResponse(BaseModel):
    """Top-level model for extraction provider output.

    Validated before any database write.  Unknown top-level fields are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[str, Field(pattern=r"^requirement-candidates-v1$")]
    candidates: list[CandidateUnit] = Field(default_factory=list)

    @field_validator("candidates")
    @classmethod
    def validate_candidate_count(cls, v: list[CandidateUnit]) -> list[CandidateUnit]:
        if len(v) > MAX_CANDIDATES_PER_DOCUMENT:
            raise ValueError(
                f"Candidate count {len(v)} exceeds "
                f"maximum {MAX_CANDIDATES_PER_DOCUMENT}"
            )
        return v


class SourceUnit(BaseModel):
    """Input source unit passed to the extractor — no storage paths or secrets."""

    model_config = ConfigDict(extra="forbid")

    sequence: int  # == DocumentPage.page_number
    page_id: str  # str(UUID) — identifier only
    unit_kind: str
    source_locator: str
    content: str  # DocumentPage.content — treated as untrusted data
    content_sha256: str


class ExtractionRequest(BaseModel):
    """Input passed to a RequirementExtractor implementation."""

    model_config = ConfigDict(extra="forbid")

    document_id: str  # str(UUID) — identifier only
    extraction_run_id: str  # str(UUID)
    extraction_schema_version: str
    prompt_version: str
    source_units: list[SourceUnit]


# ---------------------------------------------------------------------------
# Candidate content policy
# ---------------------------------------------------------------------------
# AGENTS.md: uploaded documents are untrusted data, never instructions.  A
# candidate is the first artifact derived from that untrusted text that a human
# reviewer will read in our own UI, so it must not carry markup, links, storage
# paths, executable fragments, or text shaped like instructions to an agent.
#
# These checks apply to BOTH normalized_requirement_text and the evidence
# slice.  Evidence must remain a byte-exact slice of DocumentPage.content, so
# it can never be sanitized in place — the only safe response to unsafe
# evidence is to reject.  Rejection is run-level (consistent with every other
# validation failure: no partial candidates).  Pass 2 may downgrade this to a
# per-candidate skip once a real extractor exists and the false-positive rate
# on genuine RFP prose (which does legitimately contain URLs) can be measured.

CONTENT_REJECT_HTML = "CONTENT_REJECT_HTML"
CONTENT_REJECT_URL = "CONTENT_REJECT_URL"
CONTENT_REJECT_PATH = "CONTENT_REJECT_PATH"
CONTENT_REJECT_EXECUTABLE = "CONTENT_REJECT_EXECUTABLE"
CONTENT_REJECT_INSTRUCTION = "CONTENT_REJECT_INSTRUCTION"

_UNSAFE_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Markup / tag-shaped content, including tool-call style pseudo-tags.
    (CONTENT_REJECT_HTML, re.compile(r"<\s*/?\s*[A-Za-z!][^>]*>")),
    # Any URL scheme, protocol-relative URL, or bare www host.
    (
        CONTENT_REJECT_URL,
        re.compile(
            r"(?:[A-Za-z][A-Za-z0-9+.-]*://)|(?:^|\s)//[A-Za-z0-9-]|"
            r"\b(?:javascript|data|file|vbscript)\s*:|"
            r"(?:^|\s)www\.[A-Za-z0-9-]",
            re.IGNORECASE,
        ),
    ),
    # Filesystem / storage locations: UNC, Windows drive, common POSIX roots.
    (
        CONTENT_REJECT_PATH,
        re.compile(
            r"(?:\\\\[A-Za-z0-9._-]+\\)|"
            r"(?:(?:^|\s)[A-Za-z]:[\\/])|"
            r"(?:(?:^|\s)/(?:etc|var|tmp|usr|opt|proc|root|home|app|mnt|srv)/)",
        ),
    ),
    # Executable / interpolation fragments.
    (
        CONTENT_REJECT_EXECUTABLE,
        re.compile(
            r"(?:^|\s)#!/|\$\{|\beval\s*\(|\bexec\s*\(|"
            r"\bos\.system\s*\(|\bsubprocess\b|`[^`]*`",
            re.IGNORECASE,
        ),
    ),
    # Text shaped like instructions to a model or an agent tool surface.
    (
        CONTENT_REJECT_INSTRUCTION,
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\b|"
            r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\b|"
            r"\bsystem\s+prompt\b|\byou\s+are\s+an?\s+(?:ai|assistant|agent)\b|"
            r"\bnew\s+instructions\b|\btool_call\b|\bfunction_call\b|"
            r"^\s*(?:assistant|system|user)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
)


def find_unsafe_content(text: str) -> str | None:
    """Return a fixed rejection code if ``text`` violates the content policy.

    Returns ``None`` when the text is safe.  The return value is a fixed code
    and never contains any part of ``text``, so it is safe to log and to store
    in ``ExtractionRun.failure_code``.
    """
    for code, pattern in _UNSAFE_CONTENT_PATTERNS:
        if pattern.search(text):
            return code
    return None
