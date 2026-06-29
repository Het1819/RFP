from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.database import get_default_org_and_user
from app.core.llm import RequirementDraft
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject
from app.services.extraction_service import extract_requirements_from_document


@pytest.mark.asyncio
async def test_extract_requirements_batching_and_deduplication(db):
    org_id, user_id = get_default_org_and_user(db)
    # Setup project and document
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Test Project",
        client_name="Test Client",
        status="draft",
    )
    db.add(project)
    db.commit()

    doc = Document(
        project_id=project.id,
        created_by_id=user_id,
        name="test.pdf",
        file_path="/tmp/test.pdf",
        file_type="application/pdf",
    )
    db.add(doc)
    db.commit()

    # Add 15 pages
    for i in range(1, 16):
        page = DocumentPage(
            document_id=doc.id,
            page_number=i,
            content=f"Page {i} content. Some repetitive text here.",
        )
        db.add(page)
    db.commit()

    # Mock LLM provider
    mock_llm = MagicMock()

    req_a_text = "A" * 105
    req_b_text = "A" * 100 + "diff"  # shares first 100 characters with req_a
    req_c_text = "C" * 105  # completely different

    drafts_batch_1 = [
        RequirementDraft(
            original_text=req_a_text,
            source_section="Sec 1",
            source_page=1,
            requirement_type="Technical",
            mandatory=True,
            risk_level="Low",
        ),
        RequirementDraft(
            original_text=req_c_text,
            source_section="Sec 2",
            source_page=2,
            requirement_type="Compliance",
            mandatory=False,
            risk_level="Medium",
        ),
    ]

    drafts_batch_2 = [
        RequirementDraft(
            original_text=req_b_text,
            source_section="Sec 3",
            source_page=11,
            requirement_type="Technical",
            mandatory=True,
            risk_level="Low",
        ),
        RequirementDraft(
            original_text=req_c_text,
            source_section="Sec 2",
            source_page=2,
            requirement_type="Compliance",
            mandatory=False,
            risk_level="Medium",
        ),
    ]

    mock_extract = AsyncMock()
    mock_extract.side_effect = [drafts_batch_1, drafts_batch_2]
    mock_llm.extract_requirements = mock_extract

    requirements = await extract_requirements_from_document(db, doc.id, llm=mock_llm)

    assert mock_extract.call_count == 2

    call_args_list = mock_extract.call_args_list
    batch_1_text = call_args_list[0][0][0]
    batch_2_text = call_args_list[1][0][0]

    assert "[PAGE 1]" in batch_1_text
    assert "[PAGE 10]" in batch_1_text
    assert "[PAGE 11]" not in batch_1_text

    assert "[PAGE 11]" in batch_2_text
    assert "[PAGE 15]" in batch_2_text
    assert "[PAGE 10]" not in batch_2_text

    assert len(requirements) == 2
    saved_texts = [r.original_text for r in requirements]
    assert req_a_text in saved_texts
    assert req_c_text in saved_texts
    assert req_b_text not in saved_texts
