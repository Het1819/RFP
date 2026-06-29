from unittest.mock import patch

from app.core.database import get_default_org_and_user
from app.models.document import Document
from app.models.project import ProposalProject
from app.services.project_service import process_document_background


def test_document_processing_failure_records_error(db):
    org_id, user_id = get_default_org_and_user(db)

    # Create project and document
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Failed bid",
        client_name="Stark Industries",
        status="draft",
    )
    db.add(project)
    db.commit()

    doc = Document(
        project_id=project.id,
        created_by_id=user_id,
        name="corrupt.pdf",
        file_path="/tmp/corrupt.pdf",
        file_type="application/pdf",
    )
    db.add(doc)
    db.commit()

    # Mock extract_pages to raise an exception
    with patch("app.services.project_service.extract_pages") as mock_extract:
        mock_extract.side_effect = ValueError("Corrupted PDF structure")

        from app.core.database import SessionLocal

        def mock_rollback():
            pass

        original_rollback = db.rollback
        db.rollback = mock_rollback
        try:
            process_document_background(SessionLocal, doc.id)
        finally:
            db.rollback = original_rollback

    db.expire_all()
    failed_doc = db.get(Document, doc.id)
    assert failed_doc is not None
    assert failed_doc.processing_status == "failed"
    assert failed_doc.processing_error == "Corrupted PDF structure"
