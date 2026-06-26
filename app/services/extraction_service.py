import asyncio
import uuid

from sqlalchemy.orm import Session

from app.core.llm import FakeLLMProvider, LLMProvider
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.services.project_service import log_audit_event


async def extract_requirements_from_document(
    db: Session, document_id: uuid.UUID, llm: LLMProvider | None = None
) -> list[Requirement]:
    """Service to parse extracted text from document and save requirements."""
    if llm is None:
        llm = FakeLLMProvider()

    doc = db.get(Document, document_id)
    if not doc:
        return []

    # Get page contents
    from sqlalchemy import select

    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number.asc())
    ).all()

    full_text = "\n".join([p.content for p in pages])
    drafts = await llm.extract_requirements(full_text)

    proj = db.get(ProposalProject, doc.project_id)
    org_id = proj.organization_id if proj else doc.created_by_id

    requirements = []
    for d in drafts:
        req = Requirement(
            project_id=doc.project_id,
            source_document_id=doc.id,
            original_text=d.original_text,
            source_section=d.source_section,
            source_page=d.source_page,
            requirement_type=d.requirement_type,
            mandatory=d.mandatory,
            status="NOT_STARTED",
            risk_level=d.risk_level,
        )
        db.add(req)
        requirements.append(req)

    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=doc.created_by_id,
        action="requirements_extraction",
        entity_type="Document",
        entity_id=doc.id,
        details={"extracted_count": len(requirements)},
    )

    return requirements


def run_extraction_sync(db: Session, document_id: uuid.UUID) -> None:
    """Wrapper to run extraction synchronously in background task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(extract_requirements_from_document(db, document_id))
    finally:
        loop.close()
