"""Unit tests for crash-safe clean-storage promotion service (A5d Pass 1)."""

import hashlib
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.user import User
from app.services.clean_storage_promotion import (
    PROMOTION_DESTINATION_CONFLICT,
    PROMOTION_DIGEST_MISMATCH,
    PROMOTION_SOURCE_INVALID,
    PROMOTION_SOURCE_MISSING,
    PROMOTION_TENANT_MISMATCH,
    PromotionError,
    promote_document,
)
from app.services.ingestion_state import (
    IngestionStateError,
    IngestionStatus,
    transition,
)


@pytest.fixture
def test_env(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated clean and quarantine storage directories and DB objects."""
    quarantine_dir = tmp_path / "quarantine"
    clean_dir = tmp_path / "clean"
    quarantine_dir.mkdir()
    clean_dir.mkdir()

    monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(quarantine_dir))
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(clean_dir))

    org = Organization(name="Test Org")
    db.add(org)
    db.flush()

    user = User(
        email="test@example.com",
        full_name="Test User",
        organization_id=org.id,
        hashed_password="pw",
    )
    db.add(user)
    db.flush()

    project = ProposalProject(
        name="Test Project",
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
    """Helper to create a Document with a quarantine file."""
    file_uuid = uuid.uuid4()
    rel_path = f"{file_uuid}.upload"
    abs_path = quarantine_dir / rel_path

    abs_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    doc = Document(
        project_id=project.id,
        name="test.pdf",
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


class TestIngestionStatePromotingTransitions:
    def test_valid_transitions_from_clean_pending_promotion(
        self, db: Session, test_env
    ):
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            b"test",
        )

        # CLEAN_PENDING_PROMOTION -> PROMOTING
        transition(
            db,
            doc,
            IngestionStatus.PROMOTING,
            org_id=test_env["org"].id,
            user_id=test_env["user"].id,
        )
        assert doc.ingestion_status == IngestionStatus.PROMOTING

        # PROMOTING -> CLEAN
        transition(
            db,
            doc,
            IngestionStatus.CLEAN,
            org_id=test_env["org"].id,
            user_id=test_env["user"].id,
        )
        assert doc.ingestion_status == IngestionStatus.CLEAN

    def test_promoting_to_promotion_failed_and_retry(self, db: Session, test_env):
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            b"test",
        )
        doc.ingestion_status = IngestionStatus.PROMOTING
        db.commit()

        # PROMOTING -> PROMOTION_FAILED
        transition(
            db,
            doc,
            IngestionStatus.PROMOTION_FAILED,
            org_id=test_env["org"].id,
            user_id=test_env["user"].id,
        )
        assert doc.ingestion_status == IngestionStatus.PROMOTION_FAILED

        # PROMOTION_FAILED -> PROMOTING (Retry)
        transition(
            db,
            doc,
            IngestionStatus.PROMOTING,
            org_id=test_env["org"].id,
            user_id=test_env["user"].id,
        )
        assert doc.ingestion_status == IngestionStatus.PROMOTING

    def test_direct_clean_pending_to_clean_blocked(self, db: Session, test_env):
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            b"test",
        )

        with pytest.raises(IngestionStateError):
            transition(
                db,
                doc,
                IngestionStatus.CLEAN,
                org_id=test_env["org"].id,
                user_id=test_env["user"].id,
            )


class TestCleanStoragePromotionPrimitive:
    def test_successful_promotion_flow(self, db: Session, test_env):
        content = b"Hello Clean World! " * 100
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )
        quarantine_file = test_env["quarantine_dir"] / doc.file_path

        promoted_doc = promote_document(
            db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
        )

        assert promoted_doc.ingestion_status == IngestionStatus.CLEAN
        assert promoted_doc.clean_storage_identifier is not None
        assert promoted_doc.promotion_started_at is not None
        assert promoted_doc.promotion_completed_at is not None
        assert promoted_doc.promotion_attempt_count == 1

        clean_file = test_env["clean_dir"] / promoted_doc.file_path
        assert clean_file.exists()
        assert clean_file.read_bytes() == content

        # Check permissions (0600 on POSIX)
        if os.name != "nt":
            mode = clean_file.stat().st_mode
            assert (mode & 0o777) == 0o600

        # Verify quarantine file removed
        assert not quarantine_file.exists()

    def test_missing_source_fails(self, db: Session, test_env):
        content = b"missing file content"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )
        quarantine_file = test_env["quarantine_dir"] / doc.file_path
        quarantine_file.unlink()

        with pytest.raises(PromotionError) as exc_info:
            promote_document(
                db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
            )

        assert exc_info.value.reason_code == PROMOTION_SOURCE_MISSING
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.PROMOTION_FAILED

    def test_digest_drift_fails_and_preserves_quarantine(self, db: Session, test_env):
        content = b"original content"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )
        quarantine_file = test_env["quarantine_dir"] / doc.file_path

        # Tamper quarantine content
        quarantine_file.write_bytes(b"tampered content")

        with pytest.raises(PromotionError) as exc_info:
            promote_document(
                db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
            )

        assert exc_info.value.reason_code == PROMOTION_DIGEST_MISMATCH
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.PROMOTION_FAILED
        assert quarantine_file.exists()  # Preserved

    def test_symlink_source_rejected(self, db: Session, test_env):
        if os.name == "nt":
            pytest.skip("Symlinks require admin privileges on Windows")

        content = b"real content"
        real_file = test_env["quarantine_dir"] / "real.file"
        real_file.write_bytes(content)

        symlink_file = test_env["quarantine_dir"] / "symlink.upload"
        symlink_file.symlink_to(real_file)

        doc = Document(
            project_id=test_env["project"].id,
            name="test.pdf",
            file_path="symlink.upload",
            file_type="application/pdf",
            created_by_id=test_env["user"].id,
            ingestion_status=IngestionStatus.CLEAN_PENDING_PROMOTION,
            sha256_digest=hashlib.sha256(content).hexdigest(),
            file_size_bytes=len(content),
        )
        db.add(doc)
        db.commit()

        with pytest.raises(PromotionError) as exc_info:
            promote_document(
                db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
            )

        assert exc_info.value.reason_code == PROMOTION_SOURCE_INVALID

    def test_destination_conflict_fails(self, db: Session, test_env):
        content = b"content A"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )
        doc.clean_storage_identifier = "conflict.clean"
        db.commit()

        # Pre-plant a conflicting destination file in clean dir
        conflict_file = test_env["clean_dir"] / "conflict.clean"
        conflict_file.write_bytes(b"different content B")

        with pytest.raises(PromotionError) as exc_info:
            promote_document(
                db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
            )

        assert exc_info.value.reason_code == PROMOTION_DESTINATION_CONFLICT
        assert conflict_file.read_bytes() == b"different content B"  # Never overwritten

    def test_idempotent_repromotion_succeeds(self, db: Session, test_env):
        content = b"idempotent content"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )
        clean_id = f"{uuid.uuid4()}.clean"
        doc.clean_storage_identifier = clean_id
        db.commit()

        # Pre-plant identical destination file
        existing_file = test_env["clean_dir"] / clean_id
        existing_file.write_bytes(content)

        promoted_doc = promote_document(
            db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
        )

        assert promoted_doc.ingestion_status == IngestionStatus.CLEAN
        assert existing_file.read_bytes() == content

    def test_cross_tenant_promotion_blocked(self, db: Session, test_env):
        content = b"other org content"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )

        other_org_id = uuid.uuid4()
        with pytest.raises(PromotionError) as exc_info:
            promote_document(
                db, doc.id, org_id=other_org_id, user_id=test_env["user"].id
            )

        assert exc_info.value.reason_code == PROMOTION_TENANT_MISMATCH

    def test_cleanup_failure_sets_cleanup_pending(
        self, db: Session, test_env, monkeypatch
    ):
        content = b"cleanup failure content"
        doc = create_test_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["quarantine_dir"],
            content,
        )

        # Simulate unlink error
        def mock_unlink(self):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        promoted_doc = promote_document(
            db, doc.id, org_id=test_env["org"].id, user_id=test_env["user"].id
        )

        assert promoted_doc.ingestion_status == IngestionStatus.CLEAN
        assert promoted_doc.cleanup_pending is True
