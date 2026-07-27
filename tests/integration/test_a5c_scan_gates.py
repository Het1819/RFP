"""Proves the CLEAN_PENDING_PROMOTION gates hold: a document that has
passed malware scanning and content-policy inspection but has NOT yet
been promoted to CLEAN must still be completely invisible to every
downstream evidence/retry/LLM path.

This module tests the gates themselves, not the scanner -- it builds a
CLEAN_PENDING_PROMOTION `Document` row directly (no clamd required, no
skip guard needed) with otherwise-fully-eligible metadata (doc_role=
knowledge_base, approval_status=APPROVED, processing_status=completed)
so that if any of these gates were missing, the document WOULD be picked
up. The only thing standing between it and being used as evidence is
`ingestion_status`.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.document import Document, DocumentPage
from app.models.job import ProcessingJob
from app.services import evidence_validation, retriever
from app.services.evidence_validation import EvidenceValidationError
from app.services.ingestion_state import IngestionStatus


@pytest.fixture
def _llm_call_spy(monkeypatch):
    """Spy on both FakeLLMProvider methods and assert zero invocations
    across the whole test. Patched on the class so it applies regardless
    of how/where a provider instance is constructed."""
    from app.core import llm as llm_mod

    calls: list[str] = []

    async def _spy_extract(self, text):
        calls.append("extract_requirements")
        raise AssertionError("LLM provider must never be called in this flow")

    async def _spy_draft(self, requirement_text, evidence_snippets):
        calls.append("draft_response")
        raise AssertionError("LLM provider must never be called in this flow")

    monkeypatch.setattr(llm_mod.FakeLLMProvider, "extract_requirements", _spy_extract)
    monkeypatch.setattr(llm_mod.FakeLLMProvider, "draft_response", _spy_draft)
    return calls


def _make_clean_pending_document(db, project, user) -> Document:
    """A document that is fully eligible on every dimension EXCEPT
    ingestion_status: knowledge_base role, APPROVED, processing completed,
    and has an actual DocumentPage a naive query might pick up -- CLEAN_
    PENDING_PROMOTION is the only thing that should exclude it."""
    doc = Document(
        project_id=project.id,
        name="pending-promotion.pdf",
        file_path="/quarantine/does-not-matter.upload",
        file_type="application/pdf",
        doc_role="knowledge_base",
        approval_status="APPROVED",
        processing_status="completed",
        ingestion_status=IngestionStatus.CLEAN_PENDING_PROMOTION,
        detected_content_type="application/pdf",
        sha256_digest="0" * 64,
        created_by_id=user.id,
        content="The vendor must comply with ISO 27001 certification requirements.",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    page = DocumentPage(
        document_id=doc.id,
        page_number=1,
        content="The vendor must comply with ISO 27001 certification requirements.",
    )
    db.add(page)
    db.commit()
    return doc


class TestCleanPendingPromotionExcludedFromEvidence:
    def test_excluded_from_retrieve_evidence(
        self, db, org_project_user, _llm_call_spy
    ) -> None:
        _org, project, user = org_project_user
        _make_clean_pending_document(db, project, user)

        results = retriever.retrieve_evidence(db, project.id, "ISO 27001 certification")

        assert results == []
        assert _llm_call_spy == []

    def test_rejected_by_validate_evidence_candidate(
        self, db, org_project_user, _llm_call_spy
    ) -> None:
        _org, project, user = org_project_user
        doc = _make_clean_pending_document(db, project, user)

        with pytest.raises(EvidenceValidationError) as exc_info:
            evidence_validation.validate_evidence_candidate(
                db,
                requirement_project_id=project.id,
                document_id=doc.id,
                page_number=1,
                client_snippet="The vendor must comply with ISO 27001",
            )

        assert exc_info.value.status_code == 400
        assert "security processing" in exc_info.value.detail
        assert _llm_call_spy == []

    def test_cannot_be_retried_via_retry_route(
        self, client, db, org_project_user, _llm_call_spy
    ) -> None:
        _org, project, user = org_project_user
        doc = _make_clean_pending_document(db, project, user)

        response = client.post(
            f"/projects/{project.id}/documents/{doc.id}/retry",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert db.query(ProcessingJob).count() == 0

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.CLEAN_PENDING_PROMOTION
        assert _llm_call_spy == []

    def test_zero_pages_processing_jobs_and_llm_calls_across_whole_flow(
        self, client, db, org_project_user, _llm_call_spy
    ) -> None:
        """Belt-and-braces: run all three gate checks in sequence within
        one test and confirm no DocumentPage beyond the one this fixture
        itself created, no ProcessingJob, and no LLM call anywhere."""
        _org, project, user = org_project_user
        doc = _make_clean_pending_document(db, project, user)
        pages_before = db.query(DocumentPage).filter_by(document_id=doc.id).count()
        assert pages_before == 1  # only the fixture's own page

        retriever.retrieve_evidence(db, project.id, "ISO 27001")
        with pytest.raises(EvidenceValidationError):
            evidence_validation.validate_evidence_candidate(
                db,
                requirement_project_id=project.id,
                document_id=doc.id,
                page_number=1,
                client_snippet="The vendor must comply with ISO 27001",
            )
        client.post(
            f"/projects/{project.id}/documents/{doc.id}/retry",
            follow_redirects=False,
        )

        assert db.query(DocumentPage).filter_by(document_id=doc.id).count() == 1
        assert db.query(ProcessingJob).count() == 0
        assert _llm_call_spy == []


class TestSanityDocumentIdIsReal:
    def test_document_project_scoping_still_enforced(
        self, db, org_project_user, _llm_call_spy
    ) -> None:
        """Sanity guard for the fixture itself: a random document id must
        still 404 through validate_evidence_candidate, distinct from the
        CLEAN_PENDING_PROMOTION rejection path."""
        _org, project, _user = org_project_user
        with pytest.raises(EvidenceValidationError) as exc_info:
            evidence_validation.validate_evidence_candidate(
                db,
                requirement_project_id=project.id,
                document_id=uuid.uuid4(),
                page_number=1,
                client_snippet="irrelevant snippet text here",
            )
        assert exc_info.value.status_code == 404
        assert _llm_call_spy == []
