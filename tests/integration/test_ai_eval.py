import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.llm import (
    FakeLLMProvider,
    clear_telemetry_records,
    get_telemetry_records,
    record_llm_telemetry,
)


def test_telemetry_records_success():
    """Proves telemetry records successful LLM calls without sensitive inputs."""
    clear_telemetry_records()
    record_llm_telemetry(
        "fake", "fake-model", "requirement_extraction", start_time=100.0, success=True
    )
    records = get_telemetry_records()
    assert len(records) == 1
    assert records[0]["success"] is True
    assert records[0]["provider"] == "fake"
    assert records[0]["operation"] == "requirement_extraction"
    assert records[0]["latency_ms"] >= 0


def test_telemetry_records_failure():
    """Proves telemetry records failure metadata without logging exception text."""
    clear_telemetry_records()
    record_llm_telemetry(
        "fake",
        "fake-model",
        "draft_generation",
        start_time=100.0,
        success=False,
        exception=ValueError("Sensitive text info"),
    )
    records = get_telemetry_records()
    assert len(records) == 1
    assert records[0]["success"] is False
    assert records[0]["exception_type"] == "ValueError"
    # Assert sensitive message string is not present in logged record
    assert "Sensitive text info" not in str(records[0])


def test_production_rejects_debug_payload():
    """Proves production environments block payload debug logging."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            ENABLE_LLM_DEBUG_PAYLOAD_LOGGING=True,
        )
    assert "ENABLE_LLM_DEBUG_PAYLOAD_LOGGING must be False" in str(exc_info.value)


def test_eval_fixtures_exist():
    """Proves all three golden cases are present and parse correctly."""
    fixtures_dir = Path(__file__).resolve().parent.parent.parent / "evals" / "fixtures"
    fixture_files = list(fixtures_dir.glob("*.json"))
    assert len(fixture_files) == 3
    for file_path in fixture_files:
        with open(file_path, encoding="utf-8") as f:
            case = json.load(f)
        assert "name" in case
        assert "source_text" in case
        assert "expected_requirements" in case


@pytest.mark.asyncio
async def test_offline_eval_runner_produces_metrics():
    """Proves evaluation runner calculates recall, precision, and telemetry."""
    from scripts.run_ai_eval import run_evaluation

    report = await run_evaluation(offline_mode=True)
    assert report["offline"] is True
    assert "metrics" in report
    assert "telemetry" in report
    assert report["metrics"]["recall"] > 0
    assert report["metrics"]["precision"] > 0


@pytest.mark.asyncio
async def test_injection_fixture_no_collapse():
    """Proves prompt-injection-like text does not collapse the extraction parser."""
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
