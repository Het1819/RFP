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
- **No temperature.** Current Claude models reject ``temperature`` /``top_p`` /
  ``top_k`` with a 400. Conservatism is expressed with ``output_config.effort``
  instead (see settings), which is the supported equivalent.
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
    MAX_REQUIREMENT_TEXT_LEN,
    MAX_UNCERTAINTY_REASON_LEN,
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


def _response_json_schema() -> dict[str, Any]:
    """Strict JSON schema pinned to requirement-candidates-v1.

    Written out rather than derived from the Pydantic model so the cached
    prefix stays byte-stable regardless of Pydantic's schema-generation
    details. `additionalProperties: false` at both levels is what makes the
    model unable to invent fields.
    """
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "const": SCHEMA_VERSION},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_unit_sequence": {"type": "integer"},
                        "span_start": {"type": "integer"},
                        "span_end": {"type": "integer"},
                        "requirement_text": {
                            "type": "string",
                            "maxLength": MAX_REQUIREMENT_TEXT_LEN,
                        },
                        "requirement_type": {
                            "type": ["string", "null"],
                            "enum": [
                                "functional",
                                "non_functional",
                                "compliance",
                                "security",
                                "performance",
                                "interface",
                                "operational",
                                "other",
                                None,
                            ],
                        },
                        "confidence": {"type": ["number", "null"]},
                        "uncertainty_reason": {
                            "type": ["string", "null"],
                            "maxLength": MAX_UNCERTAINTY_REASON_LEN,
                        },
                    },
                    "required": [
                        "source_unit_sequence",
                        "span_start",
                        "span_end",
                        "requirement_text",
                        "requirement_type",
                        "confidence",
                        "uncertainty_reason",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["schema_version", "candidates"],
        "additionalProperties": False,
    }


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
            "output_config": {
                "effort": settings.REQUIREMENT_EXTRACTOR_EFFORT,
                "format": {
                    "type": "json_schema",
                    "schema": _response_json_schema(),
                },
            },
        }
        # `tools` is deliberately absent: no web search, no web fetch, no code
        # execution, no computer use. Do not add one without revisiting the
        # prompt-injection analysis in extraction_prompt.

        self.usage.provider_call_count += 1
        try:
            message = client.messages.create(**params)
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
            # Strict re-validation against the Pydantic contract. The schema
            # already constrained generation; this is the belt to that braces,
            # and it is what a mocked or future transport is held to as well.
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
