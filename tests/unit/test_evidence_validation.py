"""
Unit tests for app/services/evidence_validation.py (Step 8).

All tests are synchronous and DB-free for the helper functions.
DB-dependent tests live in tests/integration/test_evidence_grounding.py.
"""

from app.services.evidence_validation import (
    CLAIM_SUPPORT_THRESHOLD,
    SNIPPET_MAX_LEN,
    SNIPPET_MIN_LEN,
    EvidenceValidationError,
    GroundingResult,
    UnsupportedClaim,
    check_claim_support,
    evidence_quote_exists_on_page,
    extract_draft_claims,
    normalize_evidence_text,
    validate_draft_grounding,
)

# ---------------------------------------------------------------------------
# normalize_evidence_text
# ---------------------------------------------------------------------------


def test_normalize_casefolds():
    assert normalize_evidence_text("Hello World") == "hello world"


def test_normalize_collapses_whitespace():
    assert normalize_evidence_text("  foo   bar  ") == "foo bar"


def test_normalize_empty_returns_empty():
    assert normalize_evidence_text("") == ""
    assert normalize_evidence_text("   ") == ""


# ---------------------------------------------------------------------------
# evidence_quote_exists_on_page
# ---------------------------------------------------------------------------


def test_quote_found_exact_match():
    page = "The system must support multi-factor authentication for all users."
    assert evidence_quote_exists_on_page("multi-factor authentication", page)


def test_quote_found_case_insensitive():
    page = "The System Must Support MFA."
    assert evidence_quote_exists_on_page("system must support mfa", page)


def test_quote_not_found():
    page = "The system must support MFA."
    assert not evidence_quote_exists_on_page("24/7 technical support", page)


def test_quote_empty_snippet_returns_false():
    assert not evidence_quote_exists_on_page("", "Some page content here.")


def test_quote_empty_page_returns_false():
    assert not evidence_quote_exists_on_page("Some snippet", "")


def test_quote_whitespace_normalised():
    page = "The system    must   support  MFA."
    snippet = "system must support MFA"
    assert evidence_quote_exists_on_page(snippet, page)


# ---------------------------------------------------------------------------
# EvidenceValidationError
# ---------------------------------------------------------------------------


def test_evidence_validation_error_attributes():
    err = EvidenceValidationError(status_code=400, detail="Test error")
    assert err.status_code == 400
    assert err.detail == "Test error"
    assert str(err) == "Test error"


# ---------------------------------------------------------------------------
# extract_draft_claims
# ---------------------------------------------------------------------------


def test_extract_claims_basic():
    draft = "The system supports MFA for all users. Vendor provides 24/7 support."
    claims = extract_draft_claims(draft)
    assert len(claims) == 2


def test_extract_claims_skips_short_fragments():
    draft = "OK. The system provides MFA authentication for all enterprise users."
    claims = extract_draft_claims(draft)
    # "OK" is too short, only the long sentence should be extracted
    assert all(len(c) >= 20 for c in claims)


def test_extract_claims_skips_boilerplate():
    draft = (
        "We will comply with the requirement. "
        "The system provides full audit logging of all user actions."
    )
    claims = extract_draft_claims(draft)
    # "We will comply..." is boilerplate and should be skipped
    assert not any("we will comply" in c.lower() for c in claims)
    assert any("audit" in c.lower() for c in claims)


def test_extract_claims_empty_draft():
    assert extract_draft_claims("") == []


def test_extract_claims_max_50():
    parts = [f"Sentence number {i} has enough words to count" for i in range(60)]
    draft = ". ".join(parts)
    claims = extract_draft_claims(draft)
    assert len(claims) <= 50


# ---------------------------------------------------------------------------
# check_claim_support
# ---------------------------------------------------------------------------


def test_claim_fully_supported():
    claim = "The system supports multi-factor authentication"
    evidence = ["The system must support multi-factor authentication for all users"]
    supported, score, _ = check_claim_support(claim, evidence)
    assert supported
    assert score >= CLAIM_SUPPORT_THRESHOLD


def test_claim_not_supported():
    claim = "The vendor provides 24/7 emergency helicopter support"
    evidence = ["The database must run on PostgreSQL 16"]
    supported, _, _ = check_claim_support(claim, evidence)
    assert not supported


def test_empty_evidence_list():
    supported, score, snippet = check_claim_support("Any claim here at all", [])
    assert not supported
    assert score == 0.0
    assert snippet is None


def test_best_evidence_returned():
    claim = "MFA is required for all users"
    evidence = [
        "The database must run on PostgreSQL",
        "MFA authentication is mandatory for all platform users",
    ]
    _, _, best = check_claim_support(claim, evidence)
    assert best == "MFA authentication is mandatory for all platform users"


def test_custom_threshold():
    claim = "support multi factor"
    evidence = ["must support multi factor authentication for users"]
    # With high threshold, might not pass
    supported_high, _, _ = check_claim_support(claim, evidence, threshold=0.9)
    supported_low, _, _ = check_claim_support(claim, evidence, threshold=0.1)
    assert supported_low  # low threshold should pass
    # high threshold may or may not pass depending on overlap — just verify no error
    _ = supported_high


# ---------------------------------------------------------------------------
# validate_draft_grounding
# ---------------------------------------------------------------------------


def test_grounding_passes_with_supported_claims():
    draft = "The system provides multi-factor authentication for all users."
    evidence = ["The system must support multi-factor authentication for all users."]
    result = validate_draft_grounding(draft, evidence)
    assert isinstance(result, GroundingResult)
    assert result.passes
    assert result.total_claims >= 1


def test_grounding_fails_with_unsupported_claims():
    draft = (
        "The vendor provides helicopter emergency response within 10 minutes. "
        "Advanced quantum encryption protects all government communications."
    )
    evidence = ["The system must support MFA."]
    result = validate_draft_grounding(draft, evidence)
    assert not result.passes
    assert len(result.unsupported_claims) > 0
    assert all(isinstance(c, UnsupportedClaim) for c in result.unsupported_claims)


def test_grounding_passes_with_no_claims():
    """A draft with no extractable claims (all boilerplate) should pass."""
    draft = "We will comply with the requirement as requested."
    evidence = []
    result = validate_draft_grounding(draft, evidence)
    assert result.passes
    assert result.total_claims == 0


def test_grounding_pass_rate_calculation():
    draft = (
        "The system provides MFA authentication for users. "
        "Advanced quantum helicopter support available immediately on demand."
    )
    evidence = ["The system must support MFA authentication for all users"]
    result = validate_draft_grounding(draft, evidence)
    assert 0.0 <= result.grounding_pass_rate <= 1.0
    expected_total = result.supported_count + len(result.unsupported_claims)
    assert result.total_claims == expected_total


def test_grounding_unsupported_claim_contains_sentence():
    draft = "Quantum encryption provides security for all planetary communications."
    evidence = ["The system must support MFA."]
    result = validate_draft_grounding(draft, evidence)
    assert not result.passes
    assert any("Quantum" in c.sentence for c in result.unsupported_claims)


# ---------------------------------------------------------------------------
# SNIPPET_MIN_LEN / SNIPPET_MAX_LEN constants
# ---------------------------------------------------------------------------


def test_snippet_constants_sane():
    assert SNIPPET_MIN_LEN > 0
    assert SNIPPET_MAX_LEN > SNIPPET_MIN_LEN
    assert SNIPPET_MAX_LEN >= 100  # at least 100 chars for meaningful evidence
