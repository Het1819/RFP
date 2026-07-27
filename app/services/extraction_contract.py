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
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "requirement-candidates-v1"

# Hard limits — must match models.extraction constants.
MAX_CANDIDATES_PER_DOCUMENT = 500
MAX_REQUIREMENT_TEXT_LEN = 2000
MAX_EVIDENCE_TEXT_LEN = 4000
MAX_UNCERTAINTY_REASON_LEN = 500

# The accepted requirement-type vocabulary.
#
# Declared as a Literal rather than enforced by an imperative validator so the
# values reach the generated JSON Schema, and therefore the wire schema sent to
# the provider. That distinction is not cosmetic: a validator is invisible to
# schema generation, so the model was previously handed an unconstrained string
# field, returned a plausible value outside this set, and the strict contract
# then rejected the *entire* response -- discarding every valid candidate
# alongside the one bad field. Constraining it on the wire lets the model get
# it right instead of being rejected afterwards.
#
# Order is fixed and meaningful: it is the enum order on the wire, and the wire
# schema must serialize deterministically for prompt-cache stability.
RequirementType = Literal[
    "functional",
    "non_functional",
    "compliance",
    "security",
    "performance",
    "interface",
    "operational",
    "other",
]

# Retained for callers that need membership checks (audit, tests, reporting).
# Derived from the Literal so the two can never drift apart.
ALLOWED_REQUIREMENT_TYPES = frozenset(get_args(RequirementType))


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
    # Literal, not str: membership is enforced by the type so it appears in the
    # generated schema. The imperative validator this replaces enforced the
    # same set but left nothing behind for schema generation to emit.
    requirement_type: Annotated[RequirementType | None, Field(default=None)]
    confidence: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)]
    uncertainty_reason: Annotated[
        str | None, Field(default=None, max_length=MAX_UNCERTAINTY_REASON_LEN)
    ]

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
# Pass 1 rejected candidates whose text or evidence contained URLs, markup-like
# characters, filesystem-looking paths, or instruction-shaped phrasing. That was
# wrong, and it was wrong in the direction that breaks the product: a real RFP
# routinely says "submit via https://portal.example.gov/bids", quotes an XML
# schema fragment, references /var/log retention, and phrases mandatory clauses
# as imperatives ("Disregard the previous revision of Section 4"). Under the old
# policy any one of those failed the entire extraction run, discarding every
# valid sibling candidate in the document.
#
# The premise was also wrong. Those strings are only dangerous if something
# acts on them, and nothing here does:
#   - no component fetches or resolves a URL found in document text;
#   - evidence and candidate text are rendered through Jinja2 autoescaping as
#     plain text, never as markup;
#   - source text is never routed back into a model as instructions, and the
#     extractor is exposed to no tools, functions, or execution surface.
# Held as inert text under those conditions, an RFP that contains a link or an
# imperative sentence is just an RFP. Refusing to extract from it protects
# nothing and loses real requirements.
#
# What survives is the narrow set of things that are not meaningful document
# prose at all: NUL and other disallowed control characters, which indicate
# binary or truncated content in a field that is supposed to hold text, and
# which cause real damage downstream (C-string truncation, PostgreSQL text
# rejection, terminal escape injection in operator tooling).
#
# Bounds, span validity, evidence-slice equality, and hash binding are all still
# enforced -- see candidate_extraction. This function governs character content
# only.

CONTENT_REJECT_CONTROL_CHARS = "CONTENT_REJECT_CONTROL_CHARS"

# Allowed: tab, newline, carriage return. Everything else in C0, plus DEL and
# the C1 block, is disallowed in a text field.
_DISALLOWED_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def find_unsafe_content(text: str) -> str | None:
    """Return a fixed rejection code if ``text`` is not usable as plain text.

    Returns ``None`` when the text is acceptable. URLs, markup-like characters,
    paths, and instruction-shaped prose are all acceptable: they are retained as
    inert text and are never fetched, rendered as markup, or interpreted as
    instructions. See the module comment above for why.

    The return value is a fixed code that never contains any part of ``text``,
    so it is safe to log and to store in a counter.
    """
    if _DISALLOWED_CONTROL_CHARS.search(text):
        return CONTENT_REJECT_CONTROL_CHARS
    return None
