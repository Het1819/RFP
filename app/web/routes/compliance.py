import logging
import uuid
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BeforeValidator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security as core_security
from app.core.config import settings
from app.core.csrf import validate_csrf_token
from app.core.database import get_db, get_default_org_and_user
from app.core.security import (
    get_project_for_org,
    get_requirement_for_org,
)
from app.core.templates import templates
from app.models.requirement import Requirement
from app.services.project_service import log_audit_event

logger = logging.getLogger(__name__)


def get_current_org_and_user(
    request: Request, db: Session
) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return core_security.get_current_org_and_user(request, db)
    except HTTPException as e:
        if e.status_code == 401 and settings.AUTH_MODE == "dev":
            core_security.check_app_env_auth()
            logger.warning(
                "Development authentication active. Falling back to default user/org."
            )
            return get_default_org_and_user(db)
        raise


class RequirementStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    DRAFTED = "DRAFTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RiskLevel(StrEnum):
    High = "High"
    Medium = "Medium"
    Low = "Low"


class RequirementType(StrEnum):
    Technical = "Technical"
    Compliance = "Compliance"
    Commercial = "Commercial"
    Procedural = "Procedural"


def empty_to_none(v: Any) -> Any:
    if v == "":
        return None
    return v


OptionalRequirementType = Annotated[
    RequirementType | None, BeforeValidator(empty_to_none)
]
OptionalRiskLevel = Annotated[RiskLevel | None, BeforeValidator(empty_to_none)]

router = APIRouter(tags=["compliance"])


# get_requirement_for_org is imported from app.core.security


@router.get("/projects/{project_id}/matrix", response_class=HTMLResponse)
def matrix_view(
    request: Request, project_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)

    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.source_page.asc(), Requirement.created_at.asc())
    ).all()

    error_msg = request.query_params.get("error")

    return templates.TemplateResponse(
        request=request,
        name="projects/matrix.html",
        context={
            "project": project,
            "requirements": requirements,
            "error_msg": error_msg,
        },
    )


@router.get("/requirements/{requirement_id}/edit", response_class=HTMLResponse)
def edit_requirement_row(
    request: Request, requirement_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)
    return templates.TemplateResponse(
        request=request,
        name="projects/matrix_row_edit.html",
        context={"req": req},
    )


@router.post(
    "/requirements/{requirement_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def update_requirement_action(
    request: Request,
    requirement_id: uuid.UUID,
    original_text: str = Form(...),
    source_section: str = Form(None),
    source_page: int = Form(None),
    requirement_type: OptionalRequirementType = Form(None),
    mandatory: bool = Form(False),
    status: RequirementStatus = Form(RequirementStatus.NOT_STARTED),
    owner_name: str = Form(None),
    proposal_section: str = Form(None),
    risk_level: OptionalRiskLevel = Form(None),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    old_details = {
        "original_text": req.original_text,
        "status": req.status,
        "mandatory": req.mandatory,
    }

    req.original_text = original_text
    req.source_section = source_section
    req.source_page = source_page
    req.requirement_type = requirement_type
    req.mandatory = mandatory
    req.status = status
    req.owner_name = owner_name
    req.proposal_section = proposal_section
    req.risk_level = risk_level
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="requirement_edit",
        entity_type="Requirement",
        entity_id=req.id,
        details={
            "old": old_details,
            "new": {
                "original_text": original_text,
                "status": status,
                "mandatory": mandatory,
            },
        },
    )

    return templates.TemplateResponse(
        request=request,
        name="projects/matrix_row.html",
        context={"req": req},
    )


@router.get("/requirements/{requirement_id}/cancel", response_class=HTMLResponse)
def cancel_edit_row(
    request: Request, requirement_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)
    return templates.TemplateResponse(
        request=request,
        name="projects/matrix_row.html",
        context={"req": req},
    )


@router.delete(
    "/requirements/{requirement_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def delete_requirement_action(
    request: Request, requirement_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)
    db.delete(req)
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="requirement_delete",
        entity_type="Requirement",
        entity_id=requirement_id,
        details={"original_text": req.original_text},
    )

    return HTMLResponse(content="")


@router.post(
    "/projects/{project_id}/matrix/merge",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def merge_requirements_action(
    project_id: uuid.UUID,
    request: Request,
    ids: list[str] = Form(...),
    db: Session = Depends(get_db),
) -> Any:
    if not ids or len(ids) < 2:
        url = f"/projects/{project_id}/matrix?error=Select at least two items to merge"
        return RedirectResponse(
            url=url,
            status_code=303,
        )

    org_id, user_id = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)
    req_ids = [uuid.UUID(i) for i in ids]

    reqs = db.scalars(
        select(Requirement).where(
            Requirement.id.in_(req_ids),
            Requirement.project_id == project.id,
        )
    ).all()

    if len(reqs) != len(req_ids):
        raise HTTPException(status_code=404, detail="No requirements found")

    merged_text = "\n[Merged] ".join([r.original_text for r in reqs])
    primary = reqs[0]

    primary.original_text = merged_text
    db.commit()

    for secondary in reqs[1:]:
        db.delete(secondary)
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="requirements_merge",
        entity_type="Requirement",
        entity_id=primary.id,
        details={"merged_ids": [str(r.id) for r in reqs]},
    )

    return RedirectResponse(url=f"/projects/{project_id}/matrix", status_code=303)


@router.post(
    "/requirements/{requirement_id}/split",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def split_requirement_action(
    requirement_id: uuid.UUID,
    request: Request,
    split_text: str = Form(...),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    secondary = Requirement(
        project_id=req.project_id,
        source_document_id=req.source_document_id,
        original_text=split_text,
        source_section=req.source_section,
        source_page=req.source_page,
        requirement_type=req.requirement_type,
        mandatory=req.mandatory,
        status="NOT_STARTED",
        risk_level=req.risk_level,
    )
    db.add(secondary)

    req.original_text = req.original_text.replace(split_text, "").strip()
    if not req.original_text:
        req.original_text = "[Split Part 1]"

    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="requirement_split",
        entity_type="Requirement",
        entity_id=req.id,
        details={"new_requirement_id": str(secondary.id)},
    )

    return RedirectResponse(url=f"/projects/{req.project_id}/matrix", status_code=303)


@router.get("/requirements/{requirement_id}/workspace", response_class=HTMLResponse)
def requirement_workspace_view(
    request: Request, requirement_id: uuid.UUID, db: Session = Depends(get_db)
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)
    project = get_project_for_org(db, req.project_id, org_id)

    # Run retrieval
    from app.services.retriever import retrieve_evidence

    q_param = request.query_params.get("q")
    if q_param is not None:
        search_query = q_param
    else:
        search_query = " ".join(req.original_text.split()[:8])
    evidence_passages = retrieve_evidence(db, req.project_id, search_query)

    # Get existing linked evidence
    from app.models.evidence import EvidenceLink

    linked_evidence = db.scalars(
        select(EvidenceLink).where(EvidenceLink.requirement_id == requirement_id)
    ).all()

    # Get draft response
    from app.models.response import DraftResponse

    draft = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == requirement_id)
        .order_by(DraftResponse.version.desc())
    ).first()

    return templates.TemplateResponse(
        request=request,
        name="projects/requirement_workspace.html",
        context={
            "project": project,
            "req": req,
            "evidence_passages": evidence_passages,
            "linked_evidence": linked_evidence,
            "draft": draft,
            "search_query": search_query,
        },
    )


@router.post(
    "/requirements/{requirement_id}/evidence/link",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def link_evidence_action(
    requirement_id: uuid.UUID,
    request: Request,
    document_id: uuid.UUID = Form(...),
    snippet: str = Form(...),
    page_number: int = Form(None),
    # client score is ignored — clamped/recomputed server-side
    score: float = Form(0.0),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    # Validate evidence candidate server-side — never trust client snippet/page/score
    from app.services.evidence_validation import (
        EvidenceValidationError,
        validate_evidence_candidate,
    )

    try:
        canonical_snippet, resolved_page = validate_evidence_candidate(
            db,
            requirement_project_id=req.project_id,
            document_id=document_id,
            page_number=page_number,
            client_snippet=snippet,
        )
    except EvidenceValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    # Client-submitted score is NEVER stored; default 0.0 (retriever sets real scores)
    _ = score  # explicitly discard
    server_score = 0.0  # TODO: compute from FTS rank if available

    from app.models.evidence import EvidenceLink

    link = EvidenceLink(
        requirement_id=requirement_id,
        document_id=document_id,
        snippet=canonical_snippet,  # server-resolved canonical snippet
        page_number=resolved_page,
        score=server_score,
    )
    db.add(link)
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="evidence_link",
        entity_type="EvidenceLink",
        entity_id=link.id,
        details={
            "requirement_id": str(requirement_id),
            "document_id": str(document_id),
            "page_number": resolved_page,
            "snippet_len": len(canonical_snippet),
        },
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.post(
    "/requirements/{requirement_id}/draft",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
async def draft_requirement_response(
    requirement_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    from app.models.evidence import EvidenceLink

    links = db.scalars(
        select(EvidenceLink).where(EvidenceLink.requirement_id == requirement_id)
    ).all()

    evidence_snippets = [
        {
            "doc_id": str(link.document_id),
            "snippet": link.snippet,
            "page_number": link.page_number,
            "score": link.score,
        }
        for link in links
    ]

    from app.core.llm import get_llm_provider

    provider = get_llm_provider()

    draft_draft = await provider.draft_response(req.original_text, evidence_snippets)

    from sqlalchemy import func

    from app.models.response import DraftResponse

    max_version = db.scalar(
        select(func.max(DraftResponse.version)).where(
            DraftResponse.requirement_id == requirement_id
        )
    )
    new_version = (max_version or 0) + 1

    draft = DraftResponse(
        requirement_id=requirement_id,
        content=draft_draft.answer_text,
        confidence=draft_draft.confidence,
        needs_evidence=draft_draft.needs_evidence,
        assumptions=draft_draft.assumptions,
        status="draft",
        version=new_version,
    )
    db.add(draft)

    if draft_draft.needs_evidence:
        req.status = "NEEDS_EVIDENCE"
    else:
        req.status = "DRAFTED"

    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="draft_generate",
        entity_type="DraftResponse",
        entity_id=draft.id,
        details={
            "requirement_id": str(requirement_id),
            "needs_evidence": draft.needs_evidence,
        },
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.post(
    "/requirements/{requirement_id}/draft/edit",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def edit_draft_response(
    requirement_id: uuid.UUID,
    request: Request,
    content: str = Form(...),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    from app.models.response import DraftResponse

    draft = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == requirement_id)
        .order_by(DraftResponse.version.desc())
    ).first()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft.content = content
    req.status = "DRAFTED"
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="draft_edit",
        entity_type="DraftResponse",
        entity_id=draft.id,
        details={"requirement_id": str(requirement_id)},
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.post(
    "/requirements/{requirement_id}/draft/approve",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def approve_draft_response(
    requirement_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    from app.models.evidence import EvidenceLink
    from app.models.response import DraftResponse
    from app.services.evidence_validation import validate_draft_grounding

    draft = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == requirement_id)
        .order_by(DraftResponse.version.desc())
    ).first()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Check grounding: collect validated evidence snippets
    links = db.scalars(
        select(EvidenceLink).where(EvidenceLink.requirement_id == requirement_id)
    ).all()

    # Block approval of mandatory requirements that have zero evidence
    if req.mandatory and not links:
        draft.status = "needs_review"
        req.status = "NEEDS_REVIEW"
        db.commit()
        log_audit_event(
            db,
            org_id=org_id,
            user_id=user_id,
            action="draft_approve_blocked",
            entity_type="DraftResponse",
            entity_id=draft.id,
            details={
                "requirement_id": str(requirement_id),
                "reason": "mandatory_requirement_no_evidence",
            },
        )
        return RedirectResponse(
            url=(
                f"/requirements/{requirement_id}/workspace"
                "?warning=mandatory_requirement_needs_evidence"
            ),
            status_code=303,
        )

    # Run grounding check against validated evidence snippets
    evidence_snippets = [link.snippet for link in links]
    grounding = validate_draft_grounding(draft.content, evidence_snippets)

    if not grounding.passes and evidence_snippets:
        # Draft has unsupported claims — route to NEEDS_REVIEW instead of APPROVED
        draft.status = "needs_review"
        req.status = "NEEDS_REVIEW"
        db.commit()
        log_audit_event(
            db,
            org_id=org_id,
            user_id=user_id,
            action="draft_approve_blocked",
            entity_type="DraftResponse",
            entity_id=draft.id,
            details={
                "requirement_id": str(requirement_id),
                "reason": "unsupported_claims",
                "unsupported_count": len(grounding.unsupported_claims),
                "grounding_pass_rate": grounding.grounding_pass_rate,
            },
        )
        return RedirectResponse(
            url=(
                f"/requirements/{requirement_id}/workspace?warning=unsupported_claims"
            ),
            status_code=303,
        )

    draft.status = "approved"
    draft.approved_by_id = user_id
    req.status = "APPROVED"
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="draft_approve",
        entity_type="DraftResponse",
        entity_id=draft.id,
        details={"requirement_id": str(requirement_id)},
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.post(
    "/requirements/{requirement_id}/draft/reject",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def reject_draft_response(
    requirement_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    from app.models.response import DraftResponse

    draft = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == requirement_id)
        .order_by(DraftResponse.version.desc())
    ).first()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft.status = "rejected"
    req.status = "REJECTED"
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="draft_reject",
        entity_type="DraftResponse",
        entity_id=draft.id,
        details={"requirement_id": str(requirement_id)},
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.post(
    "/requirements/{requirement_id}/draft/regenerate",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
async def regenerate_draft_response(
    requirement_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    return await draft_requirement_response(requirement_id, request, db)


@router.post(
    "/requirements/{requirement_id}/assign",
    response_class=RedirectResponse,
    dependencies=[Depends(validate_csrf_token)],
)
def assign_requirement_reviewer(
    requirement_id: uuid.UUID,
    request: Request,
    reviewer_name: str = Form(...),
    db: Session = Depends(get_db),
) -> Any:
    org_id, user_id = get_current_org_and_user(request, db)
    req = get_requirement_for_org(db, requirement_id, org_id)

    req.owner_name = reviewer_name
    req.status = "NEEDS_REVIEW"

    from app.models.review import ReviewTask

    task = ReviewTask(
        requirement_id=requirement_id,
        reviewer_notes=f"Routed to {reviewer_name} for review.",
        status="open",
    )
    db.add(task)
    db.commit()

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="requirement_assign",
        entity_type="Requirement",
        entity_id=requirement_id,
        details={
            "reviewer_name": reviewer_name,
            "review_task_id": str(task.id),
        },
    )

    return RedirectResponse(
        url=f"/requirements/{requirement_id}/workspace", status_code=303
    )


@router.get("/projects/{project_id}/export/matrix")
def export_compliance_matrix(
    project_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    _ = get_project_for_org(db, project_id, org_id)

    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.source_page.asc(), Requirement.created_at.asc())
    ).all()

    import io

    import xlsxwriter
    from fastapi.responses import StreamingResponse

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet("Compliance Matrix")

    header_format = workbook.add_format(
        {"bold": True, "bg_color": "#4F46E5", "font_color": "#FFFFFF", "border": 1}
    )

    headers = [
        "Requirement ID",
        "Source Section",
        "Source Page",
        "Requirement Text",
        "Type",
        "Mandatory",
        "Status",
        "Owner",
        "Proposal Section",
        "Risk Level",
    ]

    for col_num, header in enumerate(headers):
        worksheet.write(0, col_num, header, header_format)

    for row_num, req in enumerate(requirements, start=1):
        worksheet.write(row_num, 0, str(req.id))
        worksheet.write(row_num, 1, req.source_section or "")
        worksheet.write(row_num, 2, req.source_page or "")
        worksheet.write(row_num, 3, req.original_text)
        worksheet.write(row_num, 4, req.requirement_type or "")
        worksheet.write(row_num, 5, "YES" if req.mandatory else "NO")
        worksheet.write(row_num, 6, req.status)
        worksheet.write(row_num, 7, req.owner_name or "")
        worksheet.write(row_num, 8, req.proposal_section or "")
        worksheet.write(row_num, 9, req.risk_level or "")

    workbook.close()
    output.seek(0)

    filename = f"compliance_matrix_{project_id}.xlsx"
    headers_resp = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_resp,
    )


@router.get("/projects/{project_id}/export/proposal")
def export_proposal_docx(
    project_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Any:
    org_id, _ = get_current_org_and_user(request, db)
    project = get_project_for_org(db, project_id, org_id)

    from app.models.evidence import EvidenceLink
    from app.models.response import DraftResponse

    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.source_page.asc(), Requirement.created_at.asc())
    ).all()

    import io
    from datetime import UTC, datetime

    from docx import Document as DocxDocument
    from fastapi.responses import StreamingResponse

    doc = DocxDocument()
    doc.add_heading(f"Proposal Response Draft: {project.name}", level=0)
    doc.add_paragraph(f"Client: {project.client_name}")
    doc.add_paragraph(f"Date generated: {datetime.now(UTC).strftime('%Y-%m-%d')}")
    doc.add_page_break()  # type: ignore[no-untyped-call]

    has_approved = False
    for req in requirements:
        draft = db.scalars(
            select(DraftResponse)
            .where(
                DraftResponse.requirement_id == req.id,
                DraftResponse.status == "approved",
            )
            .order_by(DraftResponse.version.desc())
        ).first()

        if draft:
            has_approved = True
            doc.add_heading(f"Requirement: {req.source_section or 'General'}", level=1)
            doc.add_paragraph(req.original_text, style="Normal")

            doc.add_heading("Answer Response", level=2)
            doc.add_paragraph(draft.content)

            # Citation provenance — list all validated evidence links
            evidence_links = db.scalars(
                select(EvidenceLink).where(EvidenceLink.requirement_id == req.id)
            ).all()
            if evidence_links:
                doc.add_heading("Evidence Sources", level=3)
                for ev in evidence_links:
                    # Resolve document name for provenance
                    from app.models.document import Document as DocModel

                    ev_doc = db.scalar(
                        select(DocModel).where(DocModel.id == ev.document_id)
                    )
                    doc_name = ev_doc.name if ev_doc else str(ev.document_id)
                    page_ref = f", Page {ev.page_number}" if ev.page_number else ""
                    citation = f"[Source: {doc_name}{page_ref}]"
                    snippet_text = (
                        ev.snippet[:200] + "..."
                        if len(ev.snippet) > 200
                        else ev.snippet
                    )
                    doc.add_paragraph(f"{citation} {snippet_text}")
            doc.add_paragraph("")

    if not has_approved:
        doc.add_paragraph("No approved answers available for export.")

    output = io.BytesIO()
    doc.save(output)
    output.seek(0)

    filename = f"proposal_draft_{project_id}.docx"
    headers_resp = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers_resp,
    )
