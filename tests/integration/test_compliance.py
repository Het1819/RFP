from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.project import ProposalProject
from app.models.requirement import Requirement


def test_matrix_view_and_edit_flow(client, db):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup project and requirement
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Security matrix bid",
        client_name="Stark Industries",
        status="draft",
    )
    db.add(project)
    db.commit()

    req = Requirement(
        project_id=project.id,
        original_text="Requirement text",
        source_section="Sec 1",
        source_page=1,
        requirement_type="Technical",
        mandatory=True,
        status="NOT_STARTED",
        risk_level="Low",
    )
    db.add(req)
    db.commit()

    # 2. View compliance matrix
    response = client.get(f"/projects/{project.id}/matrix")
    assert response.status_code == 200
    assert "Requirement text" in response.text

    # 3. Get row edit form HTML
    edit_resp = client.get(f"/requirements/{req.id}/edit")
    assert edit_resp.status_code == 200
    assert "input" in edit_resp.text or "textarea" in edit_resp.text

    # 4. Post edit update
    payload = {
        "original_text": "Updated Requirement text",
        "source_section": "Sec 1.2",
        "source_page": 2,
        "requirement_type": "Security",
        "mandatory": "true",
        "status": "NEEDS_EVIDENCE",
        "owner_name": "Tony Stark",
        "proposal_section": "Prop Sec 2",
        "risk_level": "High",
    }
    update_resp = client.post(f"/requirements/{req.id}/edit", data=payload)
    assert update_resp.status_code == 200
    assert "Updated Requirement text" in update_resp.text

    # Verify DB updated
    db.expire_all()
    updated_req = db.get(Requirement, req.id)
    assert updated_req.original_text == "Updated Requirement text"
    assert updated_req.source_page == 2
    assert updated_req.status == "NEEDS_EVIDENCE"
    assert updated_req.owner_name == "Tony Stark"

    # Verify audit event logged
    audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "requirement_edit",
            AuditEvent.entity_id == req.id,
        )
    ).first()
    assert audit is not None


def test_split_and_merge_flow(client, db):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup project
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Split Merge Proj",
        client_name="Wayne Enterprises",
        status="draft",
    )
    db.add(project)
    db.commit()

    req = Requirement(
        project_id=project.id,
        original_text="This requirement has part A and split off part B.",
        source_section="Sec 2",
        source_page=1,
        requirement_type="Technical",
        mandatory=False,
        status="NOT_STARTED",
    )
    db.add(req)
    db.commit()

    # 2. Split requirement
    split_payload = {"split_text": "split off part B."}
    split_resp = client.post(
        f"/requirements/{req.id}/split", data=split_payload, follow_redirects=False
    )
    assert split_resp.status_code == 303

    # Check DB - should be two requirements now
    db.expire_all()
    reqs = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .order_by(Requirement.created_at.asc())
    ).all()
    assert len(reqs) == 2
    assert reqs[0].original_text == "This requirement has part A and"
    assert reqs[1].original_text == "split off part B."

    # Verify audit event for split
    split_audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "requirement_split",
            AuditEvent.entity_id == req.id,
        )
    ).first()
    assert split_audit is not None

    # 3. Merge them back
    merge_payload = {"ids": [str(reqs[0].id), str(reqs[1].id)]}
    merge_resp = client.post(
        f"/projects/{project.id}/matrix/merge",
        data=merge_payload,
        follow_redirects=False,
    )
    assert merge_resp.status_code == 303

    # Check DB - should be back to one requirement
    db.expire_all()
    merged_reqs = db.scalars(
        select(Requirement).where(Requirement.project_id == project.id)
    ).all()
    assert len(merged_reqs) == 1
    assert "[Merged]" in merged_reqs[0].original_text

    # Verify audit event for merge
    merge_audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "requirements_merge",
            AuditEvent.entity_id == merged_reqs[0].id,
        )
    ).first()
    assert merge_audit is not None


def test_delete_requirement(client, db):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup project
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Delete Proj",
        client_name="LexCorp",
        status="draft",
    )
    db.add(project)
    db.commit()

    req = Requirement(
        project_id=project.id,
        original_text="Will be deleted.",
        source_section="Sec 3",
        source_page=1,
        requirement_type="Technical",
        mandatory=False,
        status="NOT_STARTED",
    )
    db.add(req)
    db.commit()

    # 2. Delete requirement
    delete_resp = client.delete(f"/requirements/{req.id}")
    assert delete_resp.status_code == 200

    # Verify DB deleted
    db.expire_all()
    deleted_req = db.get(Requirement, req.id)
    assert deleted_req is None

    # Verify audit event for delete
    delete_audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "requirement_delete",
            AuditEvent.entity_id == req.id,
        )
    ).first()
    assert delete_audit is not None
