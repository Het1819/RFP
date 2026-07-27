"""Offline wire-schema compatibility tests (A5f canary remediation).

The live canary failed because the hand-built wire schema carried keywords
outside Anthropic's structured-output subset -- the request was rejected before
generation. These tests assert the properties that failure violated, against
the exact schema the adapter puts on the wire.

Everything here is offline. Three tests patch the socket layer to fail on any
DNS or TCP activity, so a regression that reintroduces a network call is caught
here rather than at a provider.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from app.core.config import settings
from app.services.anthropic_extractor import (
    PROVIDER_SCHEMA_REJECTED,
    AnthropicRequirementExtractor,
    build_wire_schema,
)
from app.services.extraction_contract import (
    ALLOWED_REQUIREMENT_TYPES,
    MAX_CANDIDATES_PER_DOCUMENT,
    MAX_REQUIREMENT_TEXT_LEN,
    MAX_UNCERTAINTY_REASON_LEN,
    SCHEMA_VERSION,
    CandidateUnit,
    ExtractionResponse,
)

# Keywords Anthropic structured outputs does not support.
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "patternProperties",
    }
)

# Anthropic's documented ceiling on optional/union parameters per schema.
MAX_UNIONS = 100


def _walk(node: Any, path: str = "$"):
    """Yield every (path, key, value) pair in the schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return build_wire_schema()


# ---------------------------------------------------------------------------
# Compatibility rules
# ---------------------------------------------------------------------------


def test_no_unsupported_keywords_anywhere(schema):
    """The exact defect that caused the canary rejection."""
    found = [
        f"{path}.{key}={value!r}"
        for path, key, value in _walk(schema)
        if key in UNSUPPORTED_KEYWORDS
    ]
    assert found == [], f"unsupported schema keywords on the wire: {found}"


def test_length_constraints_are_demoted_to_description_hints(schema):
    """maxLength must not be a *keyword*, but may survive as prose.

    The SDK moves unsupported constraints into `description` so the model is
    still told about them, while the wire schema stays inside the supported
    subset. That distinction is the whole fix: as a key it is a 400, as prose
    it is a hint. Assert both halves so a future change that starts sending it
    as a keyword again is caught.
    """
    keys = {key for _path, key, _value in _walk(schema)}
    assert "maxLength" not in keys
    assert "minLength" not in keys

    candidate = schema["$defs"]["CandidateUnit"]["properties"]
    text_description = candidate["requirement_text"].get("description", "")
    assert "maxLength" in text_description, (
        "the length hint was dropped entirely rather than demoted"
    )


def test_no_type_value_is_an_array(schema):
    """Type arrays trip a transform defect in anthropic-python 0.112.0."""
    offenders = [
        f"{path}.type={value!r}"
        for path, key, value in _walk(schema)
        if key == "type" and isinstance(value, list)
    ]
    assert offenders == [], f"type arrays present: {offenders}"


def test_no_enum_contains_null(schema):
    offenders = [
        f"{path}.enum"
        for path, key, value in _walk(schema)
        if key == "enum" and isinstance(value, list) and None in value
    ]
    assert offenders == [], f"null inside enum: {offenders}"


def test_nullable_fields_use_anyof_with_one_null_branch(schema):
    """Optional fields must be anyOf[..., {'type': 'null'}]."""
    candidate = schema["$defs"]["CandidateUnit"]["properties"]

    for name in ("requirement_type", "confidence", "uncertainty_reason"):
        field = candidate[name]
        assert "anyOf" in field, f"{name} is not expressed with anyOf"
        branches = field["anyOf"]
        null_branches = [b for b in branches if b.get("type") == "null"]
        assert len(null_branches) == 1, f"{name} must have exactly one null branch"
        non_null = [b for b in branches if b.get("type") != "null"]
        assert non_null, f"{name} has no concrete branch"
        for branch in non_null:
            assert not isinstance(branch.get("type"), list)


def test_every_object_sets_additional_properties_false(schema):
    objects = [
        (path, node)
        for path, key, node in _walk(schema)
        if key == "properties" and isinstance(node, dict)
    ]
    assert objects, "no object schemas found"

    # Both response levels specifically.
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["CandidateUnit"]["additionalProperties"] is False


def test_required_field_sets_are_correct(schema):
    # Fields with defaults are legitimately optional; the rest are required.
    assert set(schema["required"]) == {"schema_version"}
    assert set(schema["$defs"]["CandidateUnit"]["required"]) == {
        "source_unit_sequence",
        "span_start",
        "span_end",
        "requirement_text",
    }


def test_union_count_within_limit(schema):
    unions = [1 for _path, key, _value in _walk(schema) if key == "anyOf"]
    assert len(unions) <= MAX_UNIONS, f"{len(unions)} unions exceeds {MAX_UNIONS}"


def test_schema_serializes_deterministically():
    a = json.dumps(build_wire_schema(), sort_keys=True)
    b = json.dumps(build_wire_schema(), sort_keys=True)
    assert a == b


def test_schema_is_json_serializable(schema):
    json.dumps(schema)


# ---------------------------------------------------------------------------
# The adapter uses this schema / model
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 1234
    output_tokens = 56
    cache_creation_input_tokens = 78
    cache_read_input_tokens = 90


class _Msg:
    """A provider Message carrying a raw structured-output text block."""

    def __init__(self, payload: str, stop_reason: str = "end_turn") -> None:
        self.content = [_Block(payload)]
        self.stop_reason = stop_reason
        self.model = "claude-opus-5"
        self.usage = _Usage()
        self._request_id = "req_x"


def _payload(candidates: str = "[]") -> str:
    return f'{{"schema_version": "{SCHEMA_VERSION}", "candidates": {candidates}}}'


class _Messages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def parse(self, **kwargs: Any) -> Any:
        raise AssertionError(
            "adapter must use messages.create() -- parse() loses telemetry"
        )


class _Client:
    def __init__(self, outcome: Any) -> None:
        self.messages = _Messages(outcome)


def _request():
    from app.services.extraction_contract import ExtractionRequest, SourceUnit
    from app.services.extraction_prompt import PROMPT_VERSION

    return ExtractionRequest(
        document_id="00000000-0000-0000-0000-0000000000aa",
        extraction_run_id="00000000-0000-0000-0000-0000000000bb",
        extraction_schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        source_units=[
            SourceUnit(
                sequence=1,
                page_id="p1",
                unit_kind="PDF_PAGE",
                source_locator="page_1",
                content="The vendor MUST provide support.",
                content_sha256="a" * 64,
            )
        ],
    )


def test_adapter_passes_pydantic_contract_as_output_format():
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(_Msg(_payload()))
    )
    result = extractor.extract(_request())

    params = extractor._client.messages.calls[0]
    # The exact generated schema is sent; no hand-written copy, and no
    # output_format (which would move validation back inside the SDK).
    assert params["output_config"]["format"]["schema"] == build_wire_schema()
    assert "output_format" not in params
    assert "tools" not in params
    assert params["max_tokens"] == settings.REQUIREMENT_EXTRACTION_MAX_OUTPUT_TOKENS
    assert result.schema_version == SCHEMA_VERSION


def test_adapter_sets_sdk_max_retries_zero(monkeypatch):
    """Every retry must be application-level so it is counted."""
    captured: dict[str, Any] = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.messages = _Messages(_Msg(_payload()))

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    extractor = AnthropicRequirementExtractor(api_key="k", model="claude-opus-5")
    extractor.extract(_request())

    assert captured["max_retries"] == 0


def test_provider_call_ceiling_still_enforced(monkeypatch):
    import anthropic
    import httpx

    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_RETRIES", 5)
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_PROVIDER_CALLS", 1)
    monkeypatch.setattr("app.services.anthropic_extractor.time.sleep", lambda _s: None)

    timeout = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(timeout)
    )
    from app.services.requirement_extractor import ExtractionError

    with pytest.raises(ExtractionError):
        extractor.extract(_request())
    assert extractor.usage.provider_call_count == 1


# ---------------------------------------------------------------------------
# Telemetry survives local validation failure
# ---------------------------------------------------------------------------
# The second live canary lost usage, stop reason, duration and request ID
# because SDK-side validation raised before the adapter saw the Message.
# These tests pin the ordering that prevents a repeat.

# Schema-valid JSON with one deliberately invalid downstream field: the wire
# schema permits the shape, the contract rejects the confidence value.
_CONTRACT_VIOLATING = (
    f'{{"schema_version": "{SCHEMA_VERSION}", "candidates": ['
    '{"source_unit_sequence": 1, "span_start": 0, "span_end": 10, '
    '"requirement_text": "ok", "requirement_type": "compliance", '
    '"confidence": 4.2, "uncertainty_reason": null}]}'
)


def _failing_extractor() -> AnthropicRequirementExtractor:
    return AnthropicRequirementExtractor(
        api_key="k",
        model="claude-opus-5",
        client=_Client(_Msg(_CONTRACT_VIOLATING)),
    )


def test_validation_failure_maps_to_response_invalid():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INVALID
    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())

    # Not EXTRACTOR_FAILED: this is a provider-response defect, not an
    # unexpected crash in our own code.
    assert exc_info.value.code == PROVIDER_RESPONSE_INVALID


def test_usage_recorded_before_local_validation():
    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with pytest.raises(ExtractionError):
        extractor.extract(_request())

    assert extractor.usage.input_tokens == 1234
    assert extractor.usage.output_tokens == 56
    assert extractor.usage.cache_creation_input_tokens == 78
    assert extractor.usage.cache_read_input_tokens == 90


def test_request_id_and_stop_reason_retained_after_validation_error():
    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with pytest.raises(ExtractionError):
        extractor.extract(_request())

    assert extractor.usage.request_ids == ["req_x"]
    assert extractor.usage.stop_reason == "end_turn"
    assert extractor.usage.response_model == "claude-opus-5"


def test_duration_measured_on_validation_failure():
    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with pytest.raises(ExtractionError):
        extractor.extract(_request())

    # The previous implementation left this at 0 whenever an error escaped.
    assert extractor.usage.duration_ms >= 0
    assert extractor.usage.provider_call_count == 1


def test_duration_measured_on_api_exception():
    from app.services.requirement_extractor import ExtractionError

    err = _status_error(500, "server error")
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(err)
    )
    with pytest.raises(ExtractionError):
        extractor.extract(_request())
    assert extractor.usage.duration_ms >= 0


def test_validation_failure_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_PROVIDER_CALLS", 5)
    monkeypatch.setattr("app.services.anthropic_extractor.time.sleep", lambda _s: None)

    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with pytest.raises(ExtractionError):
        extractor.extract(_request())
    assert extractor.usage.provider_call_count == 1


def test_validation_diagnostic_names_fields_not_values(caplog):
    import logging

    from app.services.requirement_extractor import ExtractionError

    extractor = _failing_extractor()
    with caplog.at_level(logging.ERROR, logger="app.services.anthropic_extractor"):
        with pytest.raises(ExtractionError):
            extractor.extract(_request())

    blob = "\n".join(r.getMessage() for r in caplog.records)
    # Field location and error type are useful and safe.
    assert "confidence" in blob
    # The rejected value and the response body are not.
    assert "4.2" not in blob
    assert "requirement_text" not in blob or "ok" not in blob
    assert _CONTRACT_VIOLATING not in blob


def test_validation_error_summary_is_locations_and_types_only():
    from pydantic import ValidationError as PydanticValidationError

    from app.services.anthropic_extractor import _validation_error_summary

    try:
        ExtractionResponse.model_validate_json(_CONTRACT_VIOLATING)
    except PydanticValidationError as err:
        summary = _validation_error_summary(err)
    else:  # pragma: no cover - the payload is deliberately invalid
        pytest.fail("payload should not validate")

    assert "confidence" in summary
    assert "4.2" not in summary


# ---------------------------------------------------------------------------
# Stop reason and content shape fail closed before validation
# ---------------------------------------------------------------------------


def test_refusal_fails_closed_before_validation():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INVALID
    from app.services.requirement_extractor import ExtractionError

    extractor = AnthropicRequirementExtractor(
        api_key="k",
        model="claude-opus-5",
        client=_Client(_Msg(_payload(), stop_reason="refusal")),
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INVALID
    # Telemetry still captured even though the turn was refused.
    assert extractor.usage.stop_reason == "refusal"
    assert extractor.usage.request_ids == ["req_x"]


def test_incomplete_generation_fails_closed():
    from app.services.anthropic_extractor import PROVIDER_OUTPUT_LIMIT
    from app.services.requirement_extractor import ExtractionError

    extractor = AnthropicRequirementExtractor(
        api_key="k",
        model="claude-opus-5",
        client=_Client(_Msg('{"schema_version": "req', stop_reason="max_tokens")),
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_OUTPUT_LIMIT
    assert extractor.usage.stop_reason == "max_tokens"


def test_empty_content_fails_closed():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INCOMPLETE
    from app.services.requirement_extractor import ExtractionError

    message = _Msg(_payload())
    message.content = []
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(message)
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INCOMPLETE


def test_multiple_text_blocks_fail_closed():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INCOMPLETE
    from app.services.requirement_extractor import ExtractionError

    message = _Msg(_payload())
    message.content = [_Block(_payload()), _Block(_payload())]
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(message)
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INCOMPLETE


def test_blank_text_block_fails_closed():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INCOMPLETE
    from app.services.requirement_extractor import ExtractionError

    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(_Msg("   "))
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INCOMPLETE


def test_malformed_json_fails_closed():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INVALID
    from app.services.requirement_extractor import ExtractionError

    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(_Msg("not json"))
    )
    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# requirement_type vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(ALLOWED_REQUIREMENT_TYPES))
def test_every_canonical_requirement_type_validates(value):
    unit = CandidateUnit(
        source_unit_sequence=1,
        span_start=0,
        span_end=5,
        requirement_text="ok",
        requirement_type=value,
    )
    assert unit.requirement_type == value


@pytest.mark.parametrize(
    "value", ["mandatory", "Functional", "FUNCTIONAL", "non-functional", ""]
)
def test_unknown_or_miscased_requirement_type_rejected(value):
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="ok",
            requirement_type=value,
        )


def test_requirement_type_enum_in_pydantic_schema():
    raw = ExtractionResponse.model_json_schema()
    field = raw["$defs"]["CandidateUnit"]["properties"]["requirement_type"]
    enums = [b["enum"] for b in field["anyOf"] if "enum" in b]
    assert len(enums) == 1
    assert set(enums[0]) == ALLOWED_REQUIREMENT_TYPES


def test_requirement_type_enum_survives_transform(schema):
    field = schema["$defs"]["CandidateUnit"]["properties"]["requirement_type"]
    branches = field["anyOf"]

    enum_branches = [b for b in branches if "enum" in b]
    assert len(enum_branches) == 1
    assert set(enum_branches[0]["enum"]) == ALLOWED_REQUIREMENT_TYPES
    assert enum_branches[0]["type"] == "string"

    # Exactly one null branch, and no unconstrained string branch alongside it.
    assert [b for b in branches if b.get("type") == "null"]
    unconstrained = [
        b for b in branches if b.get("type") == "string" and "enum" not in b
    ]
    assert unconstrained == [], "an unrestricted string branch would defeat the enum"
    assert None not in enum_branches[0]["enum"]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _status_error(status: int, message: str):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return anthropic.APIStatusError(message, response=response, body=None)


def test_schema_rejection_maps_to_dedicated_code():
    err = _status_error(400, "output_config.format.schema: maxLength is not supported")
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(err)
    )
    from app.services.requirement_extractor import ExtractionError

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_SCHEMA_REJECTED


def test_schema_rejection_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_PROVIDER_CALLS", 5)
    monkeypatch.setattr("app.services.anthropic_extractor.time.sleep", lambda _s: None)

    err = _status_error(400, "invalid json_schema in output_config")
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(err)
    )
    from app.services.requirement_extractor import ExtractionError

    with pytest.raises(ExtractionError):
        extractor.extract(_request())
    assert extractor.usage.provider_call_count == 1


def test_other_400_still_maps_to_generic_invalid():
    from app.services.anthropic_extractor import PROVIDER_RESPONSE_INVALID

    err = _status_error(400, "messages: at least one message is required")
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(err)
    )
    from app.services.requirement_extractor import ExtractionError

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(_request())
    assert exc_info.value.code == PROVIDER_RESPONSE_INVALID


def test_schema_rejection_diagnostic_is_bounded_and_single_line():
    from app.services.anthropic_extractor import _sanitized_provider_detail

    noisy = _status_error(400, "schema error\n" + ("x" * 5000))
    detail = _sanitized_provider_detail(noisy)
    assert len(detail) <= 300
    assert "\n" not in detail


def test_schema_rejection_logs_operator_detail_not_source(caplog):
    import logging

    err = _status_error(400, "output_config.format.schema invalid at maxLength")
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(err)
    )
    from app.services.requirement_extractor import ExtractionError

    with caplog.at_level(logging.ERROR, logger="app.services.anthropic_extractor"):
        with pytest.raises(ExtractionError):
            extractor.extract(_request())

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "maxLength" in blob  # operator diagnostic retained
    assert "The vendor MUST provide support." not in blob  # source text never
    assert "k" != "" and "api_key" not in blob


# ---------------------------------------------------------------------------
# Downstream validation is unchanged
# ---------------------------------------------------------------------------


def test_downstream_rejects_oversized_requirement_text():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="x" * (MAX_REQUIREMENT_TEXT_LEN + 1),
        )


def test_downstream_rejects_oversized_uncertainty_reason():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="ok",
            uncertainty_reason="y" * (MAX_UNCERTAINTY_REASON_LEN + 1),
        )


def test_downstream_rejects_invalid_requirement_type():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="ok",
            requirement_type="not_a_real_type",
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_downstream_rejects_invalid_confidence(confidence):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="ok",
            confidence=confidence,
        )


def test_downstream_rejects_reversed_span():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=9,
            span_end=4,
            requirement_text="ok",
        )


def test_downstream_rejects_negative_span():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=-1,
            span_end=4,
            requirement_text="ok",
        )


def test_downstream_rejects_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CandidateUnit(
            source_unit_sequence=1,
            span_start=0,
            span_end=5,
            requirement_text="ok",
            smuggled="value",
        )

    with pytest.raises(ValidationError):
        ExtractionResponse(
            schema_version=SCHEMA_VERSION, candidates=[], smuggled="value"
        )


def test_downstream_rejects_oversized_candidate_count():
    from pydantic import ValidationError

    units = [
        CandidateUnit(
            source_unit_sequence=1, span_start=0, span_end=5, requirement_text="x"
        )
        for _ in range(MAX_CANDIDATES_PER_DOCUMENT + 1)
    ]
    with pytest.raises(ValidationError):
        ExtractionResponse(schema_version=SCHEMA_VERSION, candidates=units)


def test_downstream_rejects_wrong_schema_version():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtractionResponse(schema_version="something-else-v9", candidates=[])


# ---------------------------------------------------------------------------
# SDK transform + no network
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_sdk_transform_does_not_raise_offline(_no_network):
    """Exercise the SDK's local schema build path with zero DNS/TCP."""
    result = build_wire_schema()
    assert result["type"] == "object"
    assert "$defs" in result


def test_schema_assertions_hold_offline(_no_network):
    built = build_wire_schema()
    assert not [k for _p, k, _v in _walk(built) if k in UNSUPPORTED_KEYWORDS]
    assert not [1 for _p, k, v in _walk(built) if k == "type" and isinstance(v, list)]


def test_adapter_request_build_makes_no_network_call(_no_network):
    extractor = AnthropicRequirementExtractor(
        api_key="k", model="claude-opus-5", client=_Client(_Msg(_payload()))
    )
    assert extractor.extract(_request()).schema_version == SCHEMA_VERSION
