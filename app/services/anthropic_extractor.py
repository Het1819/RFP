"""Anthropic-backed RequirementExtractor (A5f Pass 2B1).

Production-shaped adapter behind the existing ``RequirementExtractor``
interface. It performs exactly one bounded, tool-free, structured-output
request and returns a parsed ``ExtractionResponse``; it never touches the
database, and every failure is mapped to a fixed code that carries no source
text.

Deliberate constraints
----------------------
- **No tools.** ``tools`` is never sent, so there is no web search, no web
  fetch, no code execution, and no computer use. Instruction-shaped text in a
  document has nothing to actuate.
- **Strict structured output.** ``output_config.format`` pins the response to
  the requirement-candidates-v1 JSON schema, so malformed model prose cannot
  reach the validator. Prompt-only "please return JSON" instructions are not
  used.
- **No sampling parameters.** Current Claude models reject ``temperature`` /
  ``top_p`` / ``top_k`` with a 400, so none are sent. Note what does *not*
  follow from that: ``output_config.effort`` is **not** a determinism control
  and is not a substitute for one. ``effort=low`` is a bounded cost and latency
  control. Nothing here makes the model's output deterministic.

  Correctness comes from two other places instead: strict structured output
  constrains the *shape* of the response, and the downstream span, evidence,
  and hash checks in ``candidate_extraction`` enforce *provenance*. A candidate
  survives because its span verifies against the page, never because the
  sampling was assumed to be stable.
- **Bounded everything.** Output tokens, candidate count, request timeout, and
  retry count are all settings-driven ceilings.
- **Retries only what is transient.** Timeouts, overload, and rate limits back
  off and retry; authentication, configuration, and schema failures do not.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.extraction_contract import (
    SCHEMA_VERSION,
    ExtractionRequest,
    ExtractionResponse,
)
from app.services.extraction_prompt import (
    PROMPT_VERSION,
    SYSTEM_POLICY,
    build_user_turn,
)
from app.services.requirement_extractor import (
    ExtractionError,
    RequirementExtractor,
)

logger = logging.getLogger(__name__)

# Fixed provider failure codes. Never contain prompt or source text.
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
PROVIDER_OVERLOADED = "PROVIDER_OVERLOADED"
PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
PROVIDER_RESPONSE_INCOMPLETE = "PROVIDER_RESPONSE_INCOMPLETE"
PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
PROVIDER_OUTPUT_LIMIT = "PROVIDER_OUTPUT_LIMIT"
# The request was refused because our output schema is outside the supported
# structured-output subset. Distinct from PROVIDER_RESPONSE_INVALID, which
# means the model's *response* was unusable: this one is our bug, is never
# retryable, and points an operator at the schema rather than at the model.
PROVIDER_SCHEMA_REJECTED = "PROVIDER_SCHEMA_REJECTED"

# Markers that identify a 400 as a structured-output schema rejection rather
# than some other bad request.
_SCHEMA_REJECTION_MARKERS = (
    "output_config",
    "output_format",
    "json_schema",
    "schema",
)

_TRANSIENT_CODES = frozenset(
    {PROVIDER_TIMEOUT, PROVIDER_RATE_LIMITED, PROVIDER_OVERLOADED, PROVIDER_UNAVAILABLE}
)


@dataclass
class ProviderUsage:
    """Safe usage accounting for one extraction run.

    Counters and identifiers only -- never prompt text, never model output.
    """

    provider_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    duration_ms: int = 0
    request_ids: list[str] = field(default_factory=list)


def _sanitized_provider_detail(err: Exception, limit: int = 300) -> str:
    """A bounded, single-line operator diagnostic from a provider error.

    Schema-rejection errors name schema paths and keywords, which is exactly
    what an operator needs. The text is still bounded and whitespace-collapsed
    so a verbose or unexpectedly echoing provider message cannot dump request
    content into the logs, and it is never surfaced to an end user.
    """
    text = str(getattr(err, "message", "") or err)
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _looks_like_schema_rejection(err: Exception) -> bool:
    """True when a 400 is about our output schema rather than anything else."""
    text = _sanitized_provider_detail(err, limit=1000).lower()
    return any(marker in text for marker in _SCHEMA_REJECTION_MARKERS)


def build_wire_schema() -> dict[str, Any]:
    """The exact JSON schema the SDK sends for ``ExtractionResponse``.

    Derived from the Pydantic contract via the SDK's own transform, so there
    is exactly one authoritative domain schema and no hand-maintained copy to
    drift away from it. Exposed for tests: they assert against the same bytes
    the adapter puts on the wire, not against a re-derivation.

    The wire schema contains only constraints supported by Anthropic structured
    outputs. The complete Pydantic and provenance constraints are enforced
    after parsing and before persistence.

    Concretely, the transform drops what the API's schema subset rejects and
    keeps the rest: ``maxLength`` becomes a description hint rather than a
    constraint, and ``Optional[X]`` becomes ``anyOf: [{type: X}, {type: null}]``
    rather than a ``type`` array. Both matter -- the first is rejected by the
    API outright, and the second trips a known type-array defect in
    anthropic-python 0.112.0. ``additionalProperties: false`` survives at every
    object level, which is what keeps the model from inventing fields.
    """
    from anthropic.lib._parse._transform import transform_schema

    raw = ExtractionResponse.model_json_schema()
    return transform_schema(raw)


class AnthropicRequirementExtractor(RequirementExtractor):
    """Single-call, tool-free, schema-constrained extractor.

    The client is constructed lazily so importing this module never requires
    credentials, and so a process that is not the extraction worker never
    instantiates one.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
        self._model = model or settings.REQUIREMENT_EXTRACTOR_MODEL
        self._client = client
        self.usage = ProviderUsage()

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str | None:
        return self._model

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            import anthropic
        except ImportError as err:  # pragma: no cover - dependency is declared
            raise ExtractionError(
                PROVIDER_UNAVAILABLE, "Anthropic SDK is not installed"
            ) from err

        if not self._api_key:
            raise ExtractionError(
                PROVIDER_AUTH_FAILED, "No Anthropic API key is configured"
            )

        import httpx

        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=httpx.Timeout(
                settings.REQUIREMENT_EXTRACTION_TIMEOUT_SECONDS,
                connect=settings.REQUIREMENT_EXTRACTION_CONNECT_TIMEOUT_SECONDS,
            ),
            # Retries are driven here, not by the SDK, so transient and
            # permanent failures can be told apart and counted.
            max_retries=0,
        )
        return self._client

    def _build_system(self) -> list[dict[str, Any]]:
        """Trusted policy prefix, optionally cached.

        Only this block is cached: it is identical for every document and every
        tenant. Source content lives in the user turn, after the breakpoint, so
        no customer document text is ever written to the prompt cache.
        """
        block: dict[str, Any] = {"type": "text", "text": SYSTEM_POLICY}
        if settings.REQUIREMENT_EXTRACTION_PROMPT_CACHE_ENABLED:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        started = time.monotonic()
        max_attempts = max(1, settings.REQUIREMENT_EXTRACTION_MAX_RETRIES + 1)
        max_calls = settings.REQUIREMENT_EXTRACTION_MAX_PROVIDER_CALLS

        last_error: ExtractionError | None = None

        for attempt in range(max_attempts):
            if self.usage.provider_call_count >= max_calls:
                break
            try:
                response = self._call_once(request)
                self.usage.duration_ms = int((time.monotonic() - started) * 1000)
                return response
            except ExtractionError as err:
                last_error = err
                if err.code not in _TRANSIENT_CODES or attempt == max_attempts - 1:
                    break
                delay = min(
                    settings.REQUIREMENT_EXTRACTION_RETRY_BASE_SECONDS * (2**attempt),
                    settings.REQUIREMENT_EXTRACTION_RETRY_MAX_SECONDS,
                )
                # Full jitter: spreads retries so a provider blip does not turn
                # into a synchronised thundering herd across workers.
                delay = random.uniform(0, delay)
                logger.warning(
                    "extraction.provider_retry: run_id=%s attempt=%d code=%s",
                    request.extraction_run_id,
                    attempt + 1,
                    err.code,
                )
                time.sleep(delay)

        self.usage.duration_ms = int((time.monotonic() - started) * 1000)
        raise last_error or ExtractionError(
            PROVIDER_UNAVAILABLE, "Extraction produced no provider call"
        )

    def _call_once(self, request: ExtractionRequest) -> ExtractionResponse:
        client = self._get_client()
        import anthropic

        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": settings.REQUIREMENT_EXTRACTION_MAX_OUTPUT_TOKENS,
            "system": self._build_system(),
            "messages": [
                {"role": "user", "content": build_user_turn(request.source_units)}
            ],
            # The SDK derives `output_config.format` from `output_format` and
            # merges it into whatever is passed here, so effort survives.
            "output_config": {"effort": settings.REQUIREMENT_EXTRACTOR_EFFORT},
            "output_format": ExtractionResponse,
        }
        # `tools` is deliberately absent: no web search, no web fetch, no code
        # execution, no computer use. Do not add one without revisiting the
        # prompt-injection analysis in extraction_prompt.

        self.usage.provider_call_count += 1
        try:
            # messages.parse() rather than messages.create(): the SDK builds the
            # wire schema from the Pydantic contract, stripping the keywords the
            # structured-output subset rejects, and validates the response back
            # against that same contract. One authoritative schema, no
            # hand-maintained wire copy to drift.
            message = client.messages.parse(**params)
        except anthropic.APITimeoutError as err:
            raise ExtractionError(
                PROVIDER_TIMEOUT, "Provider request timed out"
            ) from err
        except anthropic.RateLimitError as err:
            raise ExtractionError(
                PROVIDER_RATE_LIMITED, "Provider rate limited"
            ) from err
        except anthropic.AuthenticationError as err:
            raise ExtractionError(
                PROVIDER_AUTH_FAILED, "Provider rejected the credentials"
            ) from err
        except anthropic.PermissionDeniedError as err:
            raise ExtractionError(
                PROVIDER_AUTH_FAILED, "Provider denied permission for this model"
            ) from err
        except anthropic.APIStatusError as err:
            # 529 is the dedicated overload signal; other 5xx are treated as
            # transient unavailability. 4xx below is a request defect and is
            # never retried.
            status = getattr(err, "status_code", 0)
            if status == 529:
                raise ExtractionError(
                    PROVIDER_OVERLOADED, "Provider is overloaded"
                ) from err
            if status >= 500:
                raise ExtractionError(
                    PROVIDER_UNAVAILABLE, f"Provider returned status {status}"
                ) from err
            if status == 400 and _looks_like_schema_rejection(err):
                # Distinct from a bad model *response*: the request itself was
                # refused because our output schema is outside the supported
                # subset. That is our defect, not the model's, and it is not
                # retryable -- the same schema fails identically every time.
                logger.error(
                    "extraction.schema_rejected: status=%s detail=%s",
                    status,
                    _sanitized_provider_detail(err),
                )
                raise ExtractionError(
                    PROVIDER_SCHEMA_REJECTED,
                    "Provider rejected the structured-output schema",
                ) from err
            raise ExtractionError(
                PROVIDER_RESPONSE_INVALID, f"Provider rejected the request ({status})"
            ) from err
        except anthropic.APIConnectionError as err:
            raise ExtractionError(
                PROVIDER_UNAVAILABLE, "Could not reach the provider"
            ) from err

        self._record_usage(message)
        return self._parse_message(message)

    def _record_usage(self, message: Any) -> None:
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.usage.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            self.usage.cache_creation_input_tokens += int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            self.usage.cache_read_input_tokens += int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            )
        request_id = getattr(message, "_request_id", None)
        if isinstance(request_id, str) and request_id:
            # Opaque provider correlation id -- safe to retain, useful when
            # reporting a provider-side incident.
            self.usage.request_ids.append(request_id)

    def _parse_message(self, message: Any) -> ExtractionResponse:
        """Validate stop reason, then parse the constrained JSON payload."""
        stop_reason = getattr(message, "stop_reason", None)

        if stop_reason == "max_tokens":
            # Truncated JSON is not partially usable: accepting it would mean
            # persisting an arbitrary prefix of the document's requirements
            # while reporting success.
            raise ExtractionError(
                PROVIDER_OUTPUT_LIMIT,
                "Provider response hit the output token limit",
            )
        if stop_reason == "refusal":
            raise ExtractionError(
                PROVIDER_RESPONSE_INVALID, "Provider declined the request"
            )
        if stop_reason not in ("end_turn", "stop_sequence", None):
            raise ExtractionError(
                PROVIDER_RESPONSE_INCOMPLETE,
                f"Provider stopped unexpectedly ({stop_reason})",
            )

        # messages.parse() has already validated the payload against the
        # Pydantic contract and exposes the instance here.
        parsed = getattr(message, "parsed_output", None)
        if isinstance(parsed, ExtractionResponse):
            return parsed

        # Fall back to parsing the text block. This covers a transport that
        # returns an unparsed message -- including every mocked transport in
        # the test suite -- and keeps the strict contract as the last word
        # regardless of how the response arrived.
        text = self._first_text_block(message)
        if text is None:
            raise ExtractionError(
                PROVIDER_RESPONSE_INCOMPLETE, "Provider returned no text content"
            )

        import json

        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as err:
            raise ExtractionError(
                PROVIDER_RESPONSE_INVALID, "Provider returned unparseable JSON"
            ) from err

        try:
            # The wire schema constrains shape; this enforces the full
            # application contract (lengths, bounds, enum membership) that the
            # wire schema deliberately does not carry.
            return ExtractionResponse.model_validate(payload)
        except Exception as err:
            raise ExtractionError(
                PROVIDER_RESPONSE_INVALID,
                f"Provider response failed schema validation ({type(err).__name__})",
            ) from err

    @staticmethod
    def _first_text_block(message: Any) -> str | None:
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", ""))
        return None


def build_prompt_metadata() -> dict[str, str]:
    """Trusted prompt/schema identity recorded on every run."""
    return {
        "prompt_version": PROMPT_VERSION,
        "extraction_schema_version": SCHEMA_VERSION,
    }
