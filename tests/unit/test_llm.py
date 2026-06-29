from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm import AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_extract_requirements():
    mock_client = MagicMock()
    mock_messages = AsyncMock()

    mock_response = MagicMock()
    mock_content = MagicMock()
    mock_content.text = (
        '[{"original_text": "Must do X", "source_section": "1.2", '
        '"source_page": 2, "requirement_type": "Technical", '
        '"mandatory": true, "risk_level": "Low"}]'
    )
    mock_response.content = [mock_content]
    mock_messages.create.return_value = mock_response
    mock_client.messages = mock_messages

    provider = AnthropicProvider(api_key="fake-key", model="claude-3-5-sonnet-20241022")
    provider.client = mock_client

    res = await provider.extract_requirements("RFP content text")

    assert len(res) == 1
    assert res[0].original_text == "Must do X"

    mock_messages.create.assert_called_once()
    kwargs = mock_messages.create.call_args.kwargs
    assert kwargs["system"] is not None
    assert (
        "[RAW UNTRUSTED RFP TEXT]:\nRFP content text"
        in kwargs["messages"][0]["content"]
    )


@pytest.mark.asyncio
async def test_anthropic_draft_response():
    mock_client = MagicMock()
    mock_messages = AsyncMock()

    mock_response = MagicMock()
    mock_content = MagicMock()
    mock_content.text = (
        '{"answer_text": "Yes, we do X", "confidence": 0.95, '
        '"needs_evidence": false, "assumptions": "None"}'
    )
    mock_response.content = [mock_content]
    mock_messages.create.return_value = mock_response
    mock_client.messages = mock_messages

    provider = AnthropicProvider(api_key="fake-key", model="claude-3-5-sonnet-20241022")
    provider.client = mock_client

    res = await provider.draft_response(
        requirement_text="Must do X",
        evidence_snippets=[
            {"doc_name": "doc1", "page_number": 1, "snippet": "We do X"}
        ],
    )

    assert res.answer_text == "Yes, we do X"
    assert res.confidence == 0.95
    assert res.needs_evidence is False

    mock_messages.create.assert_called_once()
    kwargs = mock_messages.create.call_args.kwargs
    assert kwargs["system"] is not None
    user_content = kwargs["messages"][0]["content"]
    assert "[RAW UNTRUSTED REQUIREMENT]:\nMust do X" in user_content
    assert "[RAW UNTRUSTED EVIDENCE]:\n- [Doc: doc1, Page 1]: We do X" in user_content
