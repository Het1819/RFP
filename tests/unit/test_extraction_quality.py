"""
Async integration tests for Step 7 — extraction quality and eval runner.

Tests FakeLLMProvider async interface and the full offline eval runner.
All tests here are async and require pytest-asyncio (STRICT mode).
Sync unit tests live in test_extraction_unit.py.
"""

import pytest

from app.core.llm import FakeLLMProvider

# ---------------------------------------------------------------------------
# FakeLLMProvider async interface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_extract_returns_requirements():
    provider = FakeLLMProvider()
    text = (
        "[PAGE 1] Section 1.1\n"
        "The system must support multi-factor authentication."
    )
    reqs = await provider.extract_requirements(text)
    assert len(reqs) >= 1


@pytest.mark.asyncio
async def test_fake_provider_draft_needs_evidence_when_empty():
    provider = FakeLLMProvider()
    draft = await provider.draft_response("Some requirement", [])
    assert draft.needs_evidence is True
    assert "NEEDS_EVIDENCE" in draft.answer_text


@pytest.mark.asyncio
async def test_fake_provider_draft_grounded_with_evidence():
    provider = FakeLLMProvider()
    snippets = [{"snippet": "We support MFA", "doc_name": "Doc", "page_number": 1}]
    draft = await provider.draft_response("The system must support MFA.", snippets)
    assert draft.needs_evidence is False
    assert "MFA" in draft.answer_text or "evidence" in draft.answer_text.lower()


@pytest.mark.asyncio
async def test_fake_provider_injection_fixture_no_collapse():
    """Injection fixture must not crash the provider — at least one req extracted."""
    import json
    from pathlib import Path

    fixture_path = (
        Path(__file__).resolve().parent.parent.parent
        / "evals"
        / "fixtures"
        / "injection_rfp.json"
    )
    with open(fixture_path, encoding="utf-8") as f:
        case = json.load(f)
    provider = FakeLLMProvider()
    reqs = await provider.extract_requirements(case["source_text"])
    assert len(reqs) > 0


# ---------------------------------------------------------------------------
# Offline eval runner threshold pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_eval_passes_all_thresholds():
    """Full eval runner must pass all pilot thresholds using FakeLLMProvider."""
    from scripts.run_ai_eval import run_evaluation

    report = await run_evaluation(offline_mode=True)
    m = report["metrics"]
    assert report["thresholds_pass"] is True, (
        f"Eval did not pass thresholds: {m}"
    )
    assert m["recall"] >= 0.90, f"Recall too low: {m['recall']}"
    assert m["hallucinated_count"] == 0, (
        f"Hallucinations found: {m['hallucinated_count']}"
    )
    assert m["evidence_coverage"] >= 0.85, (
        f"Evidence coverage low: {m['evidence_coverage']}"
    )
    assert m["unsupported_claim_count"] == 0


@pytest.mark.asyncio
async def test_offline_eval_real_llm_not_called():
    """Normal pytest must not require a real LLM API key."""
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("Real API key present — test only validates offline guarantee")
    from scripts.run_ai_eval import run_evaluation

    report = await run_evaluation(offline_mode=True)
    assert report["offline"] is True
