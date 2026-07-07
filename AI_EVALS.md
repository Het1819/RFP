# AI Pipeline Quality Evaluation Guide

This document describes how to measure, verify, and maintain the quality of the RFP requirements extraction and response drafting pipeline.

## 1. Why AI Evaluations Exist
The RFP Architect MVP relies on LLMs for core value tasks:
- Extracting compliance/technical requirements from complex documents.
- Generating draft responses backed by source evidence.

Evaluations help prevent regressions such as:
- Missed requirements (low recall).
- Hallucinated requirements (low precision).
- Non-grounded or unsupported claims.
- Prompt injection bypasses.

---

## 2. Evaluation Fixtures
Golden cases reside in `evals/fixtures/*.json`. We define three baseline test cases:
1. **Simple RFP** (`simple_rfp.json`): 3-5 clear, simple security requirements.
2. **Ambiguous & Duplicate RFP** (`ambiguous_rfp.json`): Tests deduplication and page/section reference matching.
3. **Injection RFP** (`injection_rfp.json`): Tests prompt injection robustness (untrusted instruction ignore boundary).

### Adding a Golden Case
Add a JSON file under `evals/fixtures/` with this structure:
```json
{
  "name": "Case Description",
  "source_text": "[PAGE 1] raw rfp content here...",
  "expected_requirements": [
    {
      "original_text": "the exact sentence expected",
      "source_section": "Section name",
      "source_page": 1,
      "requirement_type": "Technical",
      "mandatory": true,
      "risk_level": "Medium"
    }
  ],
  "evidence_snippets": [
    {
      "doc_name": "Evidence Doc",
      "page_number": 1,
      "snippet": "grounding snippet text"
    }
  ],
  "expected_drafts": {
    "the exact requirement text to draft": {
      "needs_evidence": false,
      "answer_contains": "expected phrase in final draft"
    }
  }
}
```

---

## 3. How to Run Evaluations

### Offline Evaluation Mode (Default / Cost-Free)
Uses the deterministic `FakeLLMProvider`. Requires no API keys:
```bash
.\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline
```

### Real-Provider Evaluation Mode
Runs evaluations against the real Anthropic API:
```bash
# Set OIDC/API keys and enable real LLM eval
set ANTHROPIC_API_KEY=your-api-key
set ENABLE_REAL_LLM_EVAL=true

.\.venv\Scripts\python.exe scripts/run_ai_eval.py
```
*Note: Real-provider evals consume API tokens and incur actual costs.*

---

## 4. Evaluation Metrics
- **Recall**: Proportion of golden requirements extracted successfully (`TP / (TP + FN)`).
- **Precision**: Proportion of extracted requirements that match golden cases (`TP / (TP + FP)`).
- **F1 Score**: Harmonic mean of recall and precision.
- **Evidence Coverage Rate**: Ratio of drafted answers correctly reflecting evidence presence.
- **Unsupported Claims**: Number of answers that hallucinatory claim facts when evidence is missing.
- **Citation Page Accuracy**: Ratio of correct page number mappings.
- **Average Latency**: Latency of LLM operations in milliseconds.
- **Estimated Cost**: API costs calculated based on token counts.

### Pilot Acceptable Thresholds
- **Recall**: >= 0.90
- **Evidence Coverage**: >= 0.85
- **Unsupported Claims**: 0 (strict)
- **Citation Accuracy**: >= 0.80
