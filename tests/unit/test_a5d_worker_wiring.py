"""Unit tests for A5d Pass 2 worker wiring, queue integration, and scan trigger."""

import hashlib
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.user import User
from app.services.ingestion_state import IngestionStatus
from app.worker import promote_document_task


@pytest.fixture
def test_env(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    quarantine_dir = tmp_path / "quarantine"
    clean_dir = tmp_path / "clean"
    quarantine_dir.mkdir()
    clean_dir.mkdir()

    monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(quarantine_dir))
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(clean_dir))

    org = Organization(name="Test Org A5d")
    db.add(org)
    db.flush()

    user = User(
        email="test_a5d@example.com",
        full_name="Test User A5d",
        organization_id=org.id,
        hashed_password="pw",
    )
    db.add(user)
    db.flush()

    project = ProposalProject(
        name="Test Project A5d",
        client_name="Test Client",
        organization_id=org.id,
        created_by_id=user.id,
    )
    db.add(project)
    db.commit()

    return {
        "org": org,
        "user": user,
        "project": project,
        "quarantine_dir": quarantine_dir,
        "clean_dir": clean_dir,
    }


def create_test_doc(
    db: Session,
    project: ProposalProject,
    user: User,
    quarantine_dir: Path,
    content: bytes,
) -> Document:
    file_uuid = uuid.uuid4()
    rel_path = f"{file_uuid}.upload"
    abs_path = quarantine_dir / rel_path

    abs_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    doc = Document(
        project_id=project.id,
        name="doc_a5d.pdf",
        file_path=rel_path,
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.CLEAN_PENDING_PROMOTION,
        sha256_digest=digest,
        file_size_bytes=len(content),
    )
    db.add(doc)
    db.commit()
    return doc


class TestA5dWorkerWiring:
    @pytest.mark.asyncio
    async def test_promote_document_task_runs_promotion(self, db: Session, test_env):
        content = b"Worker wiring content test"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )

        await promote_document_task(ctx={}, document_id_str=str(doc.id))

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.CLEAN
        assert doc.clean_storage_identifier is not None
        clean_file = test_env["clean_dir"] / doc.file_path
        assert clean_file.exists()
        assert clean_file.read_bytes() == content

    @pytest.mark.asyncio
    async def test_promote_document_task_nonexistent_doc_is_noop(
        self, db: Session, test_env
    ):
        fake_id = str(uuid.uuid4())
        await promote_document_task(ctx={}, document_id_str=fake_id)  # Should not raise

    def test_scan_success_triggers_promotion_enqueue(
        self, db: Session, test_env, monkeypatch
    ):
        mock_enqueue = MagicMock()
        monkeypatch.setattr("app.core.queue.enqueue_promotion_job", mock_enqueue)

        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            b"scan content",
        )
        doc.ingestion_status = IngestionStatus.SCANNING
        doc.detected_content_type = "application/pdf"
        db.commit()

        # Simulate clean scan + policy pass
        from app.services.malware_scan import _run_content_policy

        class DummyPolicyResult:
            passed = True
            policy_version = "1.0"

        monkeypatch.setattr(
            "app.services.pdf_content_policy.check_pdf_content_policy",
            lambda path: DummyPolicyResult(),
        )

        quarantine_file = test_env["quarantine_dir"] / doc.file_path
        _run_content_policy(
            db, doc, org_id=test_env["org"].id, quarantine_path=quarantine_file
        )

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.CLEAN_PENDING_PROMOTION
        mock_enqueue.assert_called_once_with(doc.id)

    def test_rejected_scan_does_not_enqueue_promotion(
        self, db: Session, test_env, monkeypatch
    ):
        mock_enqueue = MagicMock()
        monkeypatch.setattr("app.core.queue.enqueue_promotion_job", mock_enqueue)

        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            b"rejected content",
        )
        doc.ingestion_status = IngestionStatus.SCANNING
        doc.detected_content_type = "application/pdf"
        db.commit()

        from app.services.malware_scan import _run_content_policy

        class DummyPolicyResult:
            passed = False
            reason_code = "PDF_ACTIVE_CONTENT"
            policy_version = "1.0"

        monkeypatch.setattr(
            "app.services.pdf_content_policy.check_pdf_content_policy",
            lambda path: DummyPolicyResult(),
        )

        quarantine_file = test_env["quarantine_dir"] / doc.file_path
        _run_content_policy(
            db, doc, org_id=test_env["org"].id, quarantine_path=quarantine_file
        )

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.REJECTED_CONTENT_POLICY
        mock_enqueue.assert_not_called()
