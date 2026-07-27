#!/usr/bin/env python
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Add project root to python path to import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.llm import (
    _token_overlap,
    clear_telemetry_records,
    get_llm_provider,
    get_telemetry_records,
)

# Minimum Jaccard token overlap to count a match as TP
_MATCH_THRESHOLD = 0.55


class MockDBSession:
    def __init__(self, doc, page):
        self.doc = doc
        self.page = page

    def scalar(self, statement):
        sql_str = str(statement).lower()
        if "document_pages" in sql_str:
            params = statement.compile().params
            for k, v in params.items():
                if "page" in k and isinstance(v, int) and v != self.page.page_number:
                    return None
            return self.page
        elif "documents" in sql_str:
            return self.doc
        return None


async def run_evaluation(offline_mode: bool) -> dict:
    # 1. Configure provider
    if offline_mode:
        print("Running in OFFLINE mode using FakeLLMProvider...")
        settings.LLM_PROVIDER = "fake"
        settings.APP_ENV = "development"
    else:
        print("Running in REAL-PROVIDER mode...")
        # Check settings
        if settings.LLM_PROVIDER != "anthropic":
            print("Error: LLM_PROVIDER must be 'anthropic' for real-provider evals")
            sys.exit(1)
        if not settings.ANTHROPIC_API_KEY:
            print("Error: ANTHROPIC_API_KEY is not set.")
            sys.exit(1)
        print("⚠️  Warning: Real LLM calls will be made, which may incur costs.")

    provider = get_llm_provider()

    # Load fixtures
    fixtures_dir = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
    fixture_files = list(fixtures_dir.glob("*.json"))

    if not fixture_files:
        print(f"No fixture files found in {fixtures_dir}")
        sys.exit(1)

    print(f"Loaded {len(fixture_files)} evaluation fixtures.")
    clear_telemetry_records()

    results = []
    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_unsupported_claims = 0
    total_evidence_attempts = 0
    total_evidence_covered = 0
    total_page_references_checked = 0
    total_page_references_correct = 0

    # New Step 8 integrity metrics
    total_fabricated_rejected = 0
    total_invalid_citation_rejected = 0

    total_evidence_validation_attempts = 0
    total_evidence_validation_correct = 0

    total_draft_grounding_attempts = 0
    total_draft_grounding_passed = 0
    total_draft_grounding_correct = 0
    total_unsupported_claim_count = 0

    for file_path in fixture_files:
        with open(file_path, encoding="utf-8") as f:
            case = json.load(f)

        case_name = case["name"]
        source_text = case["source_text"]
        expected_reqs = case["expected_requirements"]
        evidence_snippets = case.get("evidence_snippets", [])
        expected_drafts = case.get("expected_drafts", {})

        print(f"\nEvaluating Golden Case: '{case_name}'...")

        # A. Requirement Extraction
        extracted_reqs = await provider.extract_requirements(source_text)

        # Match extracted requirements to expected
        matched_expected = set()
        matched_extracted = set()

        tp = 0
        for i, expected in enumerate(expected_reqs):
            for j, ext in enumerate(extracted_reqs):
                if j in matched_extracted:
                    continue
                overlap = _token_overlap(expected["original_text"], ext.original_text)
                if overlap >= _MATCH_THRESHOLD:
                    matched_expected.add(i)
                    matched_extracted.add(j)
                    tp += 1
                    break

        fp = len(extracted_reqs) - len(matched_extracted)
        fn = len(expected_reqs) - len(matched_expected)

        total_tp += tp
        total_fp += fp
        total_fn += fn

        # Evaluate page references for matched pairs
        for idx in matched_expected:
            expected = expected_reqs[idx]
            for j, ext in enumerate(extracted_reqs):
                if j not in matched_extracted:
                    continue
                overlap = _token_overlap(expected["original_text"], ext.original_text)
                if overlap >= _MATCH_THRESHOLD:
                    total_page_references_checked += 1
                    if ext.source_page == expected.get("source_page"):
                        total_page_references_correct += 1
                    break

        # B. Response Drafting / Evidence Groundedness
        for req_text, expected_draft in expected_drafts.items():
            total_evidence_attempts += 1
            # Pass evidence snippets or empty list to test NEEDS_EVIDENCE
            snippets = (
                evidence_snippets if not expected_draft.get("needs_evidence") else []
            )

            draft = await provider.draft_response(req_text, snippets)

            if expected_draft.get("needs_evidence"):
                # Expecting NEEDS_EVIDENCE behavior
                if draft.needs_evidence or "NEEDS_EVIDENCE" in draft.answer_text:
                    total_evidence_covered += 1
                else:
                    total_unsupported_claims += 1
            else:
                # Expecting grounded response containing specific text
                ans_contains = expected_draft.get("answer_contains", "")
                if ans_contains.lower() in draft.answer_text.lower():
                    total_evidence_covered += 1
                else:
                    total_unsupported_claims += 1

        # C. Evidence Validation and Grounding Integrity
        integrity_cases = case.get("evidence_integrity_cases", [])
        for ic in integrity_cases:
            total_evidence_validation_attempts += 1

            from app.models.document import Document, DocumentPage
            from app.services.ingestion_state import IngestionStatus

            doc_id = uuid.uuid4()
            project_id = uuid.uuid4()

            mock_doc = Document(
                id=doc_id,
                project_id=project_id,
                name="EvalDoc",
                processing_status="completed",
                approval_status="APPROVED",
                doc_role="knowledge_base",
                content=ic["document_text"],
                ingestion_status=IngestionStatus.COMPLETED,
            )

            mock_page = DocumentPage(
                document_id=doc_id,
                page_number=ic["document_page"],
                content=ic["document_text"],
            )

            mock_db = MockDBSession(mock_doc, mock_page)

            from app.services.evidence_validation import (
                EvidenceValidationError,
                validate_evidence_candidate,
            )

            try:
                validate_evidence_candidate(
                    mock_db,
                    requirement_project_id=project_id,
                    document_id=doc_id,
                    page_number=ic["client_page"],
                    client_snippet=ic["client_snippet"],
                )
                status = "VALID"
            except EvidenceValidationError:
                status = "REJECTED"

            expected = ic["expected_status"]
            if status == expected:
                total_evidence_validation_correct += 1
                if expected == "REJECTED":
                    if "fabricated" in ic["description"].lower():
                        total_fabricated_rejected += 1
                    elif "wrong-page" in ic["description"].lower():
                        total_invalid_citation_rejected += 1
            else:
                print(
                    f"  [Integrity Fail] Case: {ic['description']}. "
                    f"Expected {expected}, got {status}"
                )

        # D. Draft Grounding Integrity
        grounding_cases = case.get("draft_grounding_cases", [])
        for gc in grounding_cases:
            from app.services.evidence_validation import (
                validate_draft_grounding,
            )

            total_draft_grounding_attempts += 1
            res = validate_draft_grounding(
                gc["draft_text"],
                gc["evidence_snippets"],
            )

            if res.passes:
                total_draft_grounding_passed += 1

            if res.passes == gc["expected_pass"]:
                total_draft_grounding_correct += 1
            else:
                print(
                    f"  [Grounding Fail] Case: {gc['description']}. "
                    f"Expected passes={gc['expected_pass']}, "
                    f"got {res.passes}"
                )

            total_unsupported_claim_count += len(res.unsupported_claims)

        results.append(
            {
                "name": case_name,
                "expected_count": len(expected_reqs),
                "extracted_count": len(extracted_reqs),
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
        )

    # C. Calculate Metrics
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    f1 = (
        2 * (precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    evidence_coverage = (
        total_evidence_covered / total_evidence_attempts
        if total_evidence_attempts > 0
        else 0.0
    )
    citation_accuracy = (
        total_page_references_correct / total_page_references_checked
        if total_page_references_checked > 0
        else 0.0
    )

    # Compute new integrity metrics
    evidence_validation_accuracy = (
        total_evidence_validation_correct / total_evidence_validation_attempts
        if total_evidence_validation_attempts > 0
        else 1.0
    )
    draft_grounding_pass_rate = (
        total_draft_grounding_passed / total_draft_grounding_attempts
        if total_draft_grounding_attempts > 0
        else 1.0
    )
    draft_grounding_accuracy = (
        total_draft_grounding_correct / total_draft_grounding_attempts
        if total_draft_grounding_attempts > 0
        else 1.0
    )

    # Retrieve telemetry stats
    records = get_telemetry_records()
    total_calls = len(records)
    total_latency = sum(r["latency_ms"] for r in records)
    avg_latency = total_latency / total_calls if total_calls > 0 else 0.0
    total_tokens = sum(
        r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in records
    )
    total_cost = sum(r.get("estimated_cost", 0.0) for r in records)
    failures = sum(1 for r in records if not r["success"])

    thresholds_pass = (
        recall >= 0.90
        and total_fp == 0
        and evidence_coverage >= 0.85
        and total_unsupported_claims == 0
        # Ensure our evidence validations are perfectly correct as well
        and evidence_validation_accuracy == 1.0
        and draft_grounding_accuracy == 1.0
    )

    eval_report = {
        "offline": report_data if False else offline_mode,  # dummy reference workaround
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "thresholds_pass": thresholds_pass,
        "metrics": {
            "recall": round(recall, 3),
            "precision": round(precision, 3),
            "f1": round(f1, 3),
            "hallucinated_count": total_fp,
            "missed_count": total_fn,
            "evidence_coverage": round(evidence_coverage, 3),
            "unsupported_claim_count": total_unsupported_claims,
            "citation_accuracy": round(citation_accuracy, 3),
            # Step 8 metrics
            "fabricated_evidence_rejected_count": total_fabricated_rejected,
            "invalid_citation_rejected_count": total_invalid_citation_rejected,
            "draft_grounding_pass_rate": round(draft_grounding_pass_rate, 3),
            "unsupported_claim_count_grounding": total_unsupported_claim_count,
            "evidence_validation_accuracy": round(evidence_validation_accuracy, 3),
        },
        "telemetry": {
            "total_calls": total_calls,
            "failures": failures,
            "avg_latency_ms": round(avg_latency, 1),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 6),
        },
        "cases": results,
    }

    return eval_report


def print_markdown_report(report: dict) -> None:
    m = report["metrics"]
    t = report["telemetry"]
    mode = "OFFLINE (Fake LLM)" if report["offline"] else "REAL LLM"
    passed = report.get("thresholds_pass", False)

    print("\n" + "=" * 60)
    print(f"              RFP ARCHITECT EVALUATION REPORT ({mode})")
    print("=" * 60)
    print(f"Timestamp: {report['timestamp']}")
    print(f"Pilot Thresholds: {'[PASS]' if passed else '[FAIL]'}")
    print("\n### Core AI Metrics:")
    print(f"- **Recall**: {m['recall']:.3f} (target >= 0.90)")
    print(f"- **Precision**: {m['precision']:.3f}")
    print(f"- **F1 Score**: {m['f1']:.3f}")
    print(
        f"- **Hallucinated Requirements (FP)**: {m['hallucinated_count']} (target = 0)"
    )
    print(f"- **Missed Requirements (FN)**: {m['missed_count']}")
    print(
        f"- **Evidence Coverage Rate**: {m['evidence_coverage']:.3f} (target >= 0.85)"
    )
    print(
        f"- **Unsupported Claims (Draft API)**: "
        f"{m['unsupported_claim_count']} (target = 0)"
    )
    print(f"- **Citation Page Accuracy**: {m['citation_accuracy']:.3f}")
    print("\n### Evidence Grounding & Integrity Metrics (Step 8):")
    print(
        f"- **Fabricated Evidence Rejected**: {m['fabricated_evidence_rejected_count']}"
    )
    print(f"- **Invalid Citations Rejected**: {m['invalid_citation_rejected_count']}")
    print(
        f"- **Draft Grounding Pass Rate**: "
        f"{m['draft_grounding_pass_rate']:.3f} (target = 1.000)"
    )
    print(
        f"- **Unsupported Claims (Grounding Engine)**: "
        f"{m['unsupported_claim_count_grounding']}"
    )
    print(
        f"- **Evidence Validation Accuracy**: "
        f"{m['evidence_validation_accuracy']:.3f} (target = 1.000)"
    )

    print("\n### LLM Observability / Telemetry Stats:")
    print(f"- **Total LLM Calls**: {t['total_calls']}")
    print(f"- **Failed Calls**: {t['failures']}")
    print(f"- **Average Latency**: {t['avg_latency_ms']} ms")
    print(f"- **Total Token Consumption**: {t['total_tokens']}")
    print(f"- **Estimated API Costs**: ${t['estimated_cost_usd']:.6f} USD")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFP Architect AI Pipeline Evaluator")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline evaluation mode using FakeLLMProvider",
    )
    args = parser.parse_args()

    # Determine mode: offline flag or ENABLE_REAL_LLM_EVAL env var
    is_offline = args.offline or (os.environ.get("ENABLE_REAL_LLM_EVAL") != "true")

    import asyncio

    report_data = asyncio.run(run_evaluation(is_offline))
    print_markdown_report(report_data)

    # Save report
    out_dir = Path(__file__).resolve().parent.parent / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"eval_report_{'offline' if is_offline else 'real'}.json"
    with open(out_file, "w", encoding="utf-8") as out_f:
        json.dump(report_data, out_f, indent=2)
    print(f"Saved detailed JSON report to {out_file}")
