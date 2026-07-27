"""RequirementExtractor provider abstraction and built-in implementations (A5f).

Only two implementations exist in Pass 1:
- ``FixtureRequirementExtractor``: deterministic, zero-network, for tests only.
- ``DisabledRequirementExtractor``: fails closed with EXTRACTOR_NOT_CONFIGURED.

Production LLM implementations are deferred to Pass 2.

Invariants
----------
- No extractor may write to the database.
- No extractor may make network calls (enforced by FixtureExtractor; the
  DisabledExtractor is structurally incapable).
- No extractor may read credentials or environment variables.
- No extractor may expose tools, functions, or code-execution surfaces.
- The extractor receives only bounded source units (no filenames, storage
  paths, secrets, or unrelated documents).
"""

from __future__ import annotations

import abc

from app.services.extraction_contract import (
    MAX_REQUIREMENT_TEXT_LEN,
    SCHEMA_VERSION,
    CandidateUnit,
    ExtractionRequest,
    ExtractionResponse,
)

# Fixed failure code for disabled/unconfigured provider.
EXTRACTOR_NOT_CONFIGURED = "EXTRACTOR_NOT_CONFIGURED"


class RequirementExtractor(abc.ABC):
    """Abstract base for all requirement extraction providers."""

    @abc.abstractmethod
    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """Run synchronous extraction.  Must not touch the database or network."""
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Short identifier logged with the ExtractionRun (no credentials)."""
        ...

    @property
    @abc.abstractmethod
    def model_name(self) -> str | None:
        """Model identifier or None for non-model providers."""
        ...


class ExtractionError(Exception):
    """Raised by an extractor when extraction cannot proceed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class DisabledRequirementExtractor(RequirementExtractor):
    """Fails closed with EXTRACTOR_NOT_CONFIGURED.

    Used as the default provider in production until a real provider is
    configured in Pass 2.  Ensures no accidental extraction occurs.
    """

    @property
    def provider_name(self) -> str:
        return "disabled"

    @property
    def model_name(self) -> str | None:
        return None

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        raise ExtractionError(
            EXTRACTOR_NOT_CONFIGURED,
            "No extraction provider is configured. "
            "Configure a real provider before running extraction.",
        )


class FixtureRequirementExtractor(RequirementExtractor):
    """Deterministic fixture extractor for tests — no network, no model calls.

    For each source unit, emits one candidate spanning the first 60 chars
    of the unit content (or full content if shorter).  The candidate text
    and evidence are derived deterministically from the page content hash
    so tests can verify exact provenance without any LLM dependency.

    NOT for production use.
    """

    @property
    def provider_name(self) -> str:
        return "fixture"

    @property
    def model_name(self) -> str | None:
        return None

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        candidates: list[CandidateUnit] = []
        for unit in request.source_units:
            content = unit.content
            if not content.strip():
                continue
            span_end = min(60, len(content))
            if span_end <= 0:
                continue
            candidates.append(
                CandidateUnit(
                    source_unit_sequence=unit.sequence,
                    span_start=0,
                    span_end=span_end,
                    requirement_text=f"[fixture] {content[:span_end].strip()}"[
                        :MAX_REQUIREMENT_TEXT_LEN
                    ],
                    requirement_type="functional",
                    confidence=0.85,
                    uncertainty_reason=None,
                )
            )
        return ExtractionResponse(
            schema_version=SCHEMA_VERSION,
            candidates=candidates,
        )
