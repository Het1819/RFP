"""Full ingestion-status lifecycle test against a REAL running `clamd`:
`ingest_uploaded_document` -> `enqueue_scan_job` (sync mode, since
QUEUE_ENABLED=false runs `run_scan_sync` inline) -> real terminal state.

Skip convention
----------------
This module requires a real `clamd` reachable at
`settings.CLAMAV_HOST`/`settings.CLAMAV_PORT`. A short, bounded
connectivity probe (`clamav_client.check_connectivity()`) runs once at
collection time; if it fails, every test in this module is skipped (not
failed) with a clear reason. To run these tests locally:

    docker compose -f compose.yml up -d clamd
    CLAMAV_HOST=localhost pytest tests/integration/test_a5c_scan_state_machine.py

The EICAR test string used below is a standard, publicly documented
anti-malware test signature -- not real malware. It is assembled from two
literal fragments at runtime (never as one unbroken literal), and is only
ever written into real quarantine storage rooted at a per-test `tmp_path`
(auto-cleaned by pytest) -- never into a tracked repository file.

EICAR fixture construction note: the malware fixture embeds the EICAR
payload as one member of a minimal, otherwise-valid DOCX package rather
than scanning it as a bare 68-byte file. This is required for two
independent reasons verified empirically against this environment: (1)
`ingest_uploaded_document` runs candidate-type detection before a
document ever reaches SCANNING, and a bare EICAR string has no PDF/DOCX
structure to pass that gate; (2) the real clamd this module runs against
only matches its "Eicar-Test-Signature" when the payload is completely
unaccompanied by any other byte in the top-level scanned stream -- even
one leading/trailing byte causes it to report clean -- so a DOCX/PDF
text-envelope wrapper defeats detection anyway. Embedding it as a zip
member instead exercises ClamAV's real archive-scanning path (it fully
decompresses and matches each member) and reliably reports FOUND. This
also happens to sidestep this host's own antivirus flagging/blocking a
bare on-disk EICAR file (confirmed independently: such a file becomes
unreadable moments after being written on this Windows host, before this
test's own code can stream it anywhere) -- the zip-embedded form is not
treated the same way by the host AV.
"""

from __future__ import annotations

import io
import zipfile

import pypdf
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import settings
from app.models.audit import AuditEvent
from app.services.document_ingestion import ingest_uploaded_document
from app.services.ingestion_state import IngestionStatus

# --- EICAR test string, assembled from fragments (see module docstring) ---
_EICAR_FRAGMENT_A = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTI"
_EICAR_FRAGMENT_B = "VIRUS-TEST-FILE!$H+H*"
_EICAR_TEST_STRING = _EICAR_FRAGMENT_A + _EICAR_FRAGMENT_B


def _clamd_reachable() -> bool:
    from app.services import clamav_client

    try:
        return clamav_client.check_connectivity()
    except Exception:
        return False


_CLAMD_AVAILABLE = _clamd_reachable()

pytestmark = pytest.mark.skipif(
    not _CLAMD_AVAILABLE,
    reason=(
        f"real clamd not reachable at {settings.CLAMAV_HOST}:{settings.CLAMAV_PORT} "
        "-- start it via `docker compose -f compose.yml up -d clamd` for this "
        "test module"
    ),
)


@pytest.fixture(autouse=True)
def _bypass_signature_freshness_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """See identical fixture in test_a5c_malware_scan_flow.py: the real
    `clamd` container's signature database age relative to "now" is an
    environment/freshclam-scheduling characteristic, not something this
    lifecycle test exists to verify (covered separately, with controlled
    timestamps, in test_malware_scan.py)."""
    monkeypatch.setattr(settings, "CLAMAV_MAX_SIGNATURE_AGE_HOURS", 24 * 3650)


def _clean_pdf_bytes() -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _eicar_docx_bytes() -> bytes:
    """A structurally valid, otherwise-clean DOCX package with one extra
    member whose content is exactly the EICAR test string -- see the
    module docstring for why this construction is used."""
    from tests.unit.test_docx_content_policy import (
        _CONTENT_TYPES_XML,
        _DOCUMENT_RELS_XML,
        _DOCUMENT_XML,
        _RELS_XML,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("word/document.xml", _DOCUMENT_XML)
        zf.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS_XML)
        zf.writestr("word/embeddings/eicar.bin", _EICAR_TEST_STRING.encode("ascii"))
    return buf.getvalue()


def _upload(content: bytes, filename: str, content_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


def _last_transition_audit_events(db, document_id) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter_by(entity_id=document_id, action="document_ingestion_transition")
        .order_by(AuditEvent.created_at.asc())
        .all()
    )


class TestCleanPdfFullLifecycle:
    def test_clean_pdf_reaches_clean_pending_promotion_with_full_metadata(
        self, db, org_project_user, tmp_path, monkeypatch
    ) -> None:
        org, project, user = org_project_user
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(tmp_path))

        upload = _upload(_clean_pdf_bytes(), "clean.pdf", "application/pdf")

        doc = ingest_uploaded_document(
            db,
            project=project,
            org_id=org.id,
            user_id=user.id,
            upload=upload,
            doc_role="rfp",
        )

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.CLEAN_PENDING_PROMOTION

        # Every intermediate scan-metadata column populated.
        assert doc.scan_started_at is not None
        assert doc.scan_completed_at is not None
        assert doc.scan_engine_version is not None
        assert doc.scan_signature_version is not None
        assert doc.scan_attempt_count == 1
        assert doc.content_policy_status == "PASSED"
        assert doc.content_policy_version is not None

        # AuditEvent trail: QUARANTINED->VALIDATING->SCANNING->
        # CLEAN_PENDING_PROMOTION, none leaking the raw filename/path.
        events = _last_transition_audit_events(db, doc.id)
        assert len(events) >= 3
        to_statuses = [e.details["to"] for e in events]
        assert IngestionStatus.SCANNING in to_statuses
        assert IngestionStatus.CLEAN_PENDING_PROMOTION in to_statuses
        for event in events:
            serialized = str(event.details)
            assert "clean.pdf" not in serialized
            assert str(tmp_path) not in serialized


class TestEicarFullLifecycle:
    def test_eicar_reaches_rejected_malware_with_safe_audit_details(
        self, db, org_project_user, tmp_path, monkeypatch
    ) -> None:
        org, project, user = org_project_user
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(tmp_path))

        # See module docstring for why the malware fixture is a DOCX
        # package with the EICAR string embedded as a member, rather than
        # a bare EICAR file.
        docx_mime = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )
        upload = _upload(_eicar_docx_bytes(), "eicar.docx", docx_mime)

        try:
            doc = ingest_uploaded_document(
                db,
                project=project,
                org_id=org.id,
                user_id=user.id,
                upload=upload,
                doc_role="rfp",
            )

            db.refresh(doc)
            assert doc.ingestion_status == IngestionStatus.REJECTED_MALWARE

            assert doc.scan_started_at is not None
            assert doc.scan_completed_at is not None
            assert doc.scan_engine_version is not None
            assert doc.scan_signature_version is not None
            assert doc.scan_attempt_count == 1
            # Content-policy inspection never runs on a malware verdict.
            assert doc.content_policy_status is None
            assert doc.content_policy_version is None

            assert doc.rejection_reason_code == "MALWARE_DETECTED"
            # Fixed-safe: no raw filename, path, or signature name leaks
            # into the user-facing rejection code/summary.
            assert "eicar" not in (doc.rejection_reason_code or "").lower()
            assert doc.operator_failure_summary is not None
            assert "eicar" not in doc.operator_failure_summary.lower()
            assert str(tmp_path) not in doc.operator_failure_summary

            events = _last_transition_audit_events(db, doc.id)
            assert len(events) >= 3
            to_statuses = [e.details["to"] for e in events]
            assert IngestionStatus.REJECTED_MALWARE in to_statuses

            malware_event = next(
                e
                for e in events
                if e.details["to"] == IngestionStatus.REJECTED_MALWARE
            )
            # rejection_reason_code on the event must be the fixed code,
            # never a raw signature name.
            assert malware_event.details["reason_code"] == "MALWARE_DETECTED"
            # The signature name (operator-forensic detail) is only ever
            # permitted inside audit_detail's own dedicated key -- it must
            # never leak into rejection_reason_code/operator_failure_summary
            # (checked above), and this document/its filename must not
            # appear anywhere in the audit trail.
            for event in events:
                serialized = str(event.details)
                assert "eicar.docx" not in serialized
                assert str(tmp_path) not in serialized
        finally:
            # No orphaned EICAR artifact should remain on disk beyond this
            # test even though tmp_path itself is auto-cleaned by pytest.
            for f in tmp_path.glob("*.upload"):
                f.unlink(missing_ok=True)
