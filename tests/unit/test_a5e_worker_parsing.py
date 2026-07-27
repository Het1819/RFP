"""Unit test suite for A5e Pass 2 worker parsing orchestration
and atomic persistence.
"""

import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.user import User
from app.parser_service.contracts import ParserResponse, ParserUnit
from app.services.document_parsing import (
    fail_parse_attempt,
    persist_parse_results,
    prepare_parse_attempt,
    run_parse_pipeline_async,
)
from app.services.ingestion_state import IngestionStatus
from app.services.parser_client import (
    ParserClientError,
    validate_parser_response,
)


@pytest.fixture
def test_env(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir(exist_ok=True)

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(clean_dir))

    org = Organization(name="Test Org A5e")
    db.add(org)
    db.flush()

    user = User(
        email="test_a5e@example.com",
        full_name="Test User A5e",
        organization_id=org.id,
        hashed_password="pw",
    )
    db.add(user)
    db.flush()

    project = ProposalProject(
        name="Test Project A5e",
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
        "clean_dir": clean_dir,
    }


def create_clean_doc(
    db: Session,
    project: ProposalProject,
    user: User,
    clean_dir: Path,
    content: bytes,
    file_name: str = "doc.pdf",
    detected_mime: str = "application/pdf",
) -> Document:
    clean_id = f"{uuid.uuid4()}.clean"
    abs_clean_path = clean_dir / clean_id
    abs_clean_path.write_bytes(content)

    digest = hashlib.sha256(content).hexdigest()

    doc = Document(
        project_id=project.id,
        name=file_name,
        file_path=clean_id,
        clean_storage_identifier=clean_id,
        file_type=detected_mime,
        detected_content_type=detected_mime,
        created_by_id=user.id,
        ingestion_status=IngestionStatus.CLEAN,
        sha256_digest=digest,
        file_size_bytes=len(content),
    )
    db.add(doc)
    db.commit()
    return doc


class TestA5eWorkerParsing:
    def test_prepare_parse_attempt_success(self, db: Session, test_env):
        doc = create_clean_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["clean_dir"],
            b"sample pdf bytes",
        )

        snapshot = prepare_parse_attempt(db, doc.id)
        assert snapshot is not None
        assert snapshot.document_id == doc.id
        assert snapshot.parse_attempt_id is not None

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.PARSING
        assert doc.parse_attempt_count == 1
        assert doc.parse_attempt_id == snapshot.parse_attempt_id

    def test_prepare_parse_attempt_non_clean_rejected(self, db: Session, test_env):
        doc = create_clean_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["clean_dir"],
            b"sample bytes",
        )
        doc.ingestion_status = IngestionStatus.SCANNING
        db.commit()

        snapshot = prepare_parse_attempt(db, doc.id)
        assert snapshot is None

    def test_persist_parse_results_atomic_insertion(self, db: Session, test_env):
        doc = create_clean_doc(
            db, test_env["project"], test_env["user"], test_env["clean_dir"], b"content"
        )
        snapshot = prepare_parse_attempt(db, doc.id)

        units = [
            ParserUnit(
                sequence=1,
                unit_kind="PDF_PAGE",
                source_locator="page_1",
                content="Page 1 text",
                content_sha256=hashlib.sha256(b"Page 1 text").hexdigest(),
            ),
            ParserUnit(
                sequence=2,
                unit_kind="PDF_PAGE",
                source_locator="page_2",
                content="Page 2 text",
                content_sha256=hashlib.sha256(b"Page 2 text").hexdigest(),
            ),
        ]
        resp = ParserResponse(
            protocol_version="1.0",
            parser_name="rfp-isolated-parser",
            parser_version="1.0.0",
            document_type="PDF",
            units=units,
            total_units=2,
            total_characters=len("Page 1 text") + len("Page 2 text"),
        )

        persist_parse_results(db, snapshot, resp)

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.COMPLETED
        assert len(doc.pages) == 2
        assert doc.pages[0].page_number == 1
        assert doc.pages[0].unit_kind == "PDF_PAGE"
        assert doc.pages[0].source_locator == "page_1"
        assert doc.pages[0].content == "Page 1 text"
        assert doc.pages[1].page_number == 2
        assert doc.pages[1].unit_kind == "PDF_PAGE"
        assert doc.pages[1].source_locator == "page_2"

    def test_stale_attempt_id_rejected_on_persist(self, db: Session, test_env):
        doc = create_clean_doc(
            db, test_env["project"], test_env["user"], test_env["clean_dir"], b"content"
        )
        snapshot = prepare_parse_attempt(db, doc.id)

        # Simulate a second attempt taking over
        doc.parse_attempt_id = "new-attempt-token"
        db.commit()

        resp = ParserResponse(
            protocol_version="1.0",
            parser_name="rfp-isolated-parser",
            parser_version="1.0.0",
            document_type="PDF",
            units=[],
            total_units=0,
            total_characters=0,
        )

        persist_parse_results(db, snapshot, resp)

        db.refresh(doc)
        # Should stay in current state and not transition
        assert doc.ingestion_status == IngestionStatus.PARSING
        assert doc.parse_attempt_id == "new-attempt-token"

    def test_docx_logical_chunk_persistence(self, db: Session, test_env):
        doc = create_clean_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["clean_dir"],
            b"docx bytes",
            file_name="doc.docx",
            detected_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        snapshot = prepare_parse_attempt(db, doc.id)

        units = [
            ParserUnit(
                sequence=1,
                unit_kind="DOCX_LOGICAL_CHUNK",
                source_locator="chunk_1",
                content="Chunk 1 text",
                content_sha256=hashlib.sha256(b"Chunk 1 text").hexdigest(),
            )
        ]
        resp = ParserResponse(
            protocol_version="1.0",
            parser_name="rfp-isolated-parser",
            parser_version="1.0.0",
            document_type="DOCX",
            units=units,
            total_units=1,
            total_characters=len("Chunk 1 text"),
        )

        persist_parse_results(db, snapshot, resp)

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.COMPLETED
        assert len(doc.pages) == 1
        assert doc.pages[0].unit_kind == "DOCX_LOGICAL_CHUNK"
        assert doc.pages[0].source_locator == "chunk_1"

    def test_fail_parse_attempt_leaves_no_partial_pages(self, db: Session, test_env):
        doc = create_clean_doc(
            db, test_env["project"], test_env["user"], test_env["clean_dir"], b"content"
        )
        snapshot = prepare_parse_attempt(db, doc.id)

        fail_parse_attempt(
            db, snapshot, error_code="PARSER_TIMEOUT", summary="Parsing timed out"
        )

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.PARSE_FAILED
        assert doc.parse_error_code == "PARSER_TIMEOUT"
        assert len(doc.pages) == 0

    @pytest.mark.asyncio
    async def test_full_pipeline_async_mocked(self, db: Session, test_env, monkeypatch):
        from sqlalchemy.orm import sessionmaker

        session_factory = sessionmaker(bind=db.bind)
        monkeypatch.setattr(
            "app.services.document_parsing.SessionLocal", session_factory
        )

        mock_send = AsyncMock()
        mock_send.return_value = ParserResponse(
            protocol_version="1.0",
            parser_name="rfp-isolated-parser",
            parser_version="1.0.0",
            document_type="PDF",
            units=[
                ParserUnit(
                    sequence=1,
                    unit_kind="PDF_PAGE",
                    source_locator="page_1",
                    content="Extracted page 1",
                    content_sha256=hashlib.sha256(b"Extracted page 1").hexdigest(),
                )
            ],
            total_units=1,
            total_characters=len("Extracted page 1"),
        )
        monkeypatch.setattr(
            "app.services.document_parsing.send_document_for_parsing", mock_send
        )

        doc = create_clean_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["clean_dir"],
            b"pdf data",
        )

        await run_parse_pipeline_async(doc.id)

        db.expire_all()
        fetched_doc = db.get(Document, doc.id)
        assert fetched_doc is not None
        assert fetched_doc.ingestion_status == IngestionStatus.COMPLETED
        assert len(fetched_doc.pages) == 1
        assert fetched_doc.pages[0].content == "Extracted page 1"

    def test_containment_and_digest_drift_rejection(self, db: Session, test_env):
        from app.services.parser_client import _resolve_and_verify_clean_file

        clean_dir = test_env["clean_dir"]
        file_path = clean_dir / "valid.clean"
        file_path.write_bytes(b"content")
        digest = hashlib.sha256(b"content").hexdigest()

        # 1. Valid file passes
        file_obj, size = _resolve_and_verify_clean_file(
            "valid.clean", digest, len(b"content")
        )
        assert size == len(b"content")
        file_obj.close()

        # 2. Path containment rejection
        with pytest.raises(ParserClientError) as exc_info:
            _resolve_and_verify_clean_file("../outside.txt", digest, len(b"content"))
        assert exc_info.value.code == "PARSER_INPUT_CHANGED"

        # 3. Digest drift rejection
        with pytest.raises(ParserClientError) as exc_info:
            _resolve_and_verify_clean_file("valid.clean", "0" * 64, len(b"content"))
        assert exc_info.value.code == "PARSER_INPUT_CHANGED"

    def test_validate_parser_response_duplicate_locators(self):
        units = [
            ParserUnit(
                sequence=1,
                unit_kind="PDF_PAGE",
                source_locator="page_1",
                content="P1",
                content_sha256="a" * 64,
            ),
            ParserUnit(
                sequence=2,
                unit_kind="PDF_PAGE",
                source_locator="page_1",
                content="P2",
                content_sha256="b" * 64,
            ),
        ]
        resp_data = {
            "protocol_version": "1.0",
            "parser_name": "rfp-isolated-parser",
            "parser_version": "1.0.0",
            "document_type": "PDF",
            "units": [u.model_dump() for u in units],
            "total_units": 2,
            "total_characters": 4,
        }
        with pytest.raises(ParserClientError) as exc_info:
            validate_parser_response(resp_data)
        assert exc_info.value.code == "PARSER_RESPONSE_INVALID"

    def test_enqueue_failure_leaves_clean_state(self, db: Session, test_env):
        from app.core.queue import _handle_parse_enqueue_failure
        from app.models.audit import AuditEvent

        doc = create_clean_doc(
            db,
            test_env["project"],
            test_env["user"],
            test_env["clean_dir"],
            b"pdf data",
        )
        _handle_parse_enqueue_failure(doc.id, RuntimeError("Redis offline"))

        db.expire_all()
        fetched_doc = db.get(Document, doc.id)
        assert fetched_doc is not None
        assert fetched_doc.ingestion_status == IngestionStatus.CLEAN

        audit = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_id == doc.id)
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        assert audit is not None
        assert audit.action == "document_parse_enqueue_failed"
        assert audit.details["reason_code"] == "PARSE_QUEUE_FAILED"
