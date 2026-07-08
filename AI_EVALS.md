# AI Pipeline Quality Evaluation Guide

This document describes how to measure, verify, and maintain the quality of the RFP requirements extraction and response drafting pipeline.

---

## 1. Why AI Evaluations Exist

The RFP Architect MVP relies on LLMs for core value tasks:
- Extracting compliance/technical requirements from complex documents.
- Generating draft responses backed by source evidence.

Evaluations help prevent regressions such as:
- Missed requirements (low recall).
- Hallucinated requirements (low precision).
- Non-grounded or unsupported claims.
- Prompt injection bypasses.
- Deduplication failures.
- Citation/page-number errors.

---

## 2. Current Eval Results (Step 8 — Offline)

| Metric | Result | Threshold |
|--------|--------|-----------|
| Recall | 1.000 | >= 0.90 |
| Precision | 1.000 | — |
| F1 Score | 1.000 | — |
| Hallucinated Requirements | 0 | = 0 |
| Missed Requirements | 0 | — |
| Evidence Coverage | 1.000 | >= 0.85 |
| Unsupported Claims | 0 | = 0 |
| Citation Page Accuracy | 1.000 | — |
| Fabricated Evidence Rejected | 1 | — |
| Invalid Citations Rejected | 1 | — |
| Draft Grounding Pass Rate | 0.500 | — |
| Evidence Validation Accuracy | 1.000 | = 1.000 |

**Pilot Thresholds: PASS**

---

## 3. Evaluation Fixtures

Golden cases reside in `evals/fixtures/*.json`. Three baseline test cases are defined:

1. **Simple RFP** (`simple_rfp.json`): 3 security requirements with must/shall/should triggers.
2. **Ambiguous & Duplicate RFP** (`ambiguous_rfp.json`): Two PostgreSQL 16 references across different pages/sections (tests deduplication and page tracking).
3. **Injection RFP** (`injection_rfp.json`): Contains a prompt-injection sentence mixed with a legitimate SSO requirement (tests guardrail and salvage logic).

### Adding a Golden Case

Add a JSON file under `evals/fixtures/` with this structure:
```json
{
  "name": "Case Description",
  "source_text": "[PAGE 1] Section X.Y: Title\nrequirement sentence here...",
  "expected_requirements": [
    {
      "original_text": "the exact sentence expected",
      "source_section": "Section X.Y",
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
    "the exact requirement text": {
      "needs_evidence": false,
      "answer_contains": "expected phrase in final draft"
    }
  }
}
```

**Rules for new golden cases:**
- Use realistic RFP language (must/shall/should/required).
- Use `[PAGE N]` markers to define page boundaries.
- Include expected `source_page` in each expected requirement.
- Do not add cases that only work when matching is loosened — thresholds must not drop below targets.
- Test prompt-injection robustness by including at least one fixture with injection text.

---

## 4. Eval Matching Logic

Requirements are matched between extracted and expected using **Jaccard token-overlap** on `normalize_text()` output:

- `normalize_text()` casefolds, expands aliases (e.g. `postgres` → `postgresql`, `mfa` → `multi-factor authentication`), strips punctuation, and collapses whitespace.
- Two requirements match if their Jaccard overlap >= **0.55** (configurable via `_MATCH_THRESHOLD`).
- Each extracted requirement is matched at most once (prevents double-counting).

This is more robust than substring matching but avoids the subjectivity of LLM-as-judge.

---

## 5. How Deduplication Works

After extraction, `deduplicate_requirements()` merges near-duplicate requirements:

- Uses the same `_token_overlap()` function with threshold **0.75** (higher than eval matching, to avoid false merges).
- When two requirements overlap >= 0.75, the first occurrence is kept and receives an `extraction_warnings` entry recording the merged duplicate.
- Both source page/section references are recorded in the warning.
- Distinct requirements (overlap < 0.75) are always preserved.

**Example:** `"The database must run on PostgreSQL 16"` and `"The database must use PostgreSQL 16"` will be merged. But `"The system must support MFA"` and `"The vendor shall provide 24/7 support"` will not be merged.

---

## 6. How Prompt-Injection Text Is Handled

The FakeLLMProvider (and the real Anthropic system prompt) contain explicit guardrails:

**Detection patterns** (compiled regex, checked per line and per sentence):
- `"ignore (previous|all|above|prior) instruction"`
- `"disregard (previous|all|above|prior)"`
- `"you are now"`
- `"act as (if|a|an)"`
- `"mark all requirements compliant"`
- `"system prompt"` / `"override the system"`

**Behavior:**
1. If a line starts with injection text, the line is rejected.
2. **Salvage logic**: If a line contains injection text followed by a legitimate requirement (e.g. `"Ignore previous instructions. The system must support SSO."`), sentences after the injection sentence are still checked and valid requirements are extracted.
3. Rejected sentences are logged as `extraction_rejected_injection` warnings (text prefix only, not full content).

**Real Anthropic provider**: The `_SYSTEM_EXTRACT` prompt explicitly tells the model not to extract meta-instructions or jailbreak text, and requires a source quote for every extracted requirement.

---

## 7. How Citation Validity Is Scored

- Each extracted requirement has a `source_page` set from the nearest preceding `[PAGE N]` marker.
- Eval counts a citation as correct when `ext.source_page == expected_req.source_page`.
- If a requirement has no page marker context, `source_page` will be `None` and the citation is scored as incorrect.
- Invalid/missing citations are NOT silently ignored — they reduce `citation_accuracy`.
- The pipeline does NOT fabricate page numbers.

---

## 8. How to Run Evaluations

### Offline Evaluation Mode (Default / Cost-Free)

Uses the deterministic `FakeLLMProvider`. Requires no API keys:
```bash
.\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline
```

### Real-Provider Evaluation Mode

Runs evaluations against the real Anthropic API:
```bash
set ANTHROPIC_API_KEY=your-api-key
set ENABLE_REAL_LLM_EVAL=true
.\.venv\Scripts\python.exe scripts/run_ai_eval.py
```

> **Note:** Real-provider evals consume API tokens and incur actual costs.

---

## 9. What Not to Log

- **Never** log raw prompt text.
- **Never** log raw LLM completions.
- **Never** log uploaded document text or customer RFP content.
- **Never** log API keys.
- `ENABLE_LLM_DEBUG_PAYLOAD_LOGGING=true` is **forbidden in production** (enforced by Settings validator).
- Telemetry logs only: `provider`, `model`, `operation`, `latency_ms`, `success`, `exception_type`, `input_tokens`, `output_tokens`, `estimated_cost`.

---

## 10. Eval Metrics Reference

| Metric | Formula | What it measures |
|--------|---------|-----------------|
| Recall | TP / (TP + FN) | Fraction of expected requirements extracted |
| Precision | TP / (TP + FP) | Fraction of extractions that are correct |
| F1 | 2 × P × R / (P + R) | Harmonic mean of precision and recall |
| Evidence Coverage | Covered / Attempted | Draft responses with correct evidence grounding |
| Unsupported Claims | Count | Drafts claiming facts without evidence |
| Citation Accuracy | Correct / Checked | Source page number accuracy |
| Hallucinated Count | FP | Extracted requirements with no golden match |
| Missed Count | FN | Golden requirements not found in extraction |
| Fabricated Evidence Rejected | Count | Fabricated evidence snippets successfully rejected |
| Invalid Citations Rejected | Count | Wrong-page citations successfully rejected |
| Draft Grounding Pass Rate | Pass / Attempt | Percentage of drafts that are fully grounded |
| Evidence Validation Accuracy | Correct / Attempt | Accuracy of evidence validation classifying candidates |

---

## 11. Pilot Acceptable Thresholds

| Metric | Threshold |
|--------|-----------|
| Recall | >= 0.90 |
| Hallucinated Requirements | = 0 |
| Evidence Coverage | >= 0.85 |
| Unsupported Claims | = 0 |
| Citation Accuracy | >= 0.80 (documented; not yet a hard block) |
| Evidence Validation Accuracy | = 1.000 |
| Draft Grounding Accuracy | = 1.000 (checker logic classification accuracy) |

---

## 12. Evidence Validation and Citation Integrity (Step 8)

To harden evidence grounding, all evidence linking inputs are validated server-side:
- **Project Boundary Verification**: Ensures any document linked belongs to the exact same organization and proposal project as the requirement.
- **Document Integrity Checks**: Rejects deleted, unprocessed, failed, or unapproved documents.
- **Page Verification**: Validates that the requested `page_number` exists.
- **Snippet Content Verification**: Normalizes whitespace and case on the snippet and page, and verifies the snippet exists as a substring.
- **Score Dismissal**: Discards client-provided scores and clamps/computes them server-side.

---

## 13. Draft Grounding Checker

Draft responses are evaluated against validated evidence links to flag unsupported claims:
- **Sentences Extraction**: Splits draft text into individual sentences and removes common boilerplate compliance phrases (e.g., "we will comply").
- **Jaccard Token Overlap**: Compares each sentence's tokens to the validated evidence snippets. A sentence must overlap by at least **0.20** Jaccard index to be supported.
- **Approval Gate Block**: Drafts with unsupported claims or mandatory requirements without any evidence links cannot be approved (routed to `NEEDS_REVIEW`).

---

## 14. Known Limitations & How to Add Eval Cases

- **Sentence splitting**: Currently uses a standard period-based regex. Complex formatting (bullet points, decimal numbers) might split incorrectly.
- **Semantic alignment**: Exact matching is performed. Synonyms or rephrasings are not resolved unless explicitly present in the document.

### Adding Evidence-Grounding Cases
Add `evidence_integrity_cases` and `draft_grounding_cases` arrays to any golden case JSON file under `evals/fixtures/` as detailed in `evidence_integrity_rfp.json`.

---

## 15. CI/CD Evaluation Gate

To prevent quality regressions, the offline AI evaluation suite is executed on every pull request and commit:
1. **GitHub Actions**: The `ai-evals` job runs the offline suite and fails the build if the pipeline falls below the pilot thresholds.
2. **Artifact Preservation**: Detailed evaluation reports are stored as a CI artifact (`ai-eval-report`) for inspection.

