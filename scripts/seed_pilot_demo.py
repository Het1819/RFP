# ruff: noqa: E402
# scripts/seed_pilot_demo.py
# Synthetic pilot data seeding script for staging/development rehearsal.
#
# Usage:
#   python scripts/seed_pilot_demo.py

import os
import sys
import uuid
from datetime import UTC, datetime

# Ensure the project root is in the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

# Safety Check: Refuse to run in production
if settings.APP_ENV == "production":
    print("CRITICAL: Refusing to run pilot data seed script in production!")
    sys.exit(1)

print(f"Seeding database for environment: {settings.APP_ENV}")

from app.core.database import SessionLocal
from app.models.document import Document
from app.models.evidence import EvidenceLink
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse
from app.models.user import User

db = SessionLocal()

try:
    # 1. Create Pilot Organization
    pilot_org_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    org = db.query(Organization).filter_by(id=pilot_org_id).first()
    if not org:
        org = Organization(id=pilot_org_id, name="Acme Pilot Corporation")
        db.add(org)
        db.commit()
        print(f"Created Pilot Org: {org.name} ({org.id})")
    else:
        print(f"Org already exists: {org.name}")

    # 2. Create Pilot User
    pilot_user_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    user = db.query(User).filter_by(id=pilot_user_id).first()
    if not user:
        user = User(
            id=pilot_user_id,
            organization_id=org.id,
            email="pilot@example.com",
            hashed_password="fake-pbkdf2-sha256-hash-for-now",
            full_name="Alice Pilot Reviewer",
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Created Pilot User: {user.email} ({user.id})")
    else:
        print(f"User already exists: {user.email}")

    # 3. Create Pilot Project
    pilot_project_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    project = db.query(ProposalProject).filter_by(id=pilot_project_id).first()
    if not project:
        project = ProposalProject(
            id=pilot_project_id,
            organization_id=org.id,
            name="Staging Rehearsal Cloud RFP",
            client_name="Staging Enterprise Client",
            due_date=datetime.now(UTC),
            status="active",
            created_by_id=user.id,
        )
        db.add(project)
        db.commit()
        print(f"Created Pilot Project: {project.name}")
    else:
        print(f"Project already exists: {project.name}")

    # 4. Create Pilot RFP Document
    doc_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    doc = db.query(Document).filter_by(id=doc_id).first()
    if not doc:
        doc = Document(
            id=doc_id,
            project_id=project.id,
            name="security_requirements_v2.pdf",
            file_path="storage/uploads/security_requirements_v2.pdf",
            file_type="pdf",
            doc_role="rfp",
            content=(
                "Sec-1. Data at rest must be encrypted. "
                "Sec-2. Production logs must not expose credentials."
            ),
            processing_status="succeeded",
            created_by_id=user.id,
        )
        db.add(doc)
        db.commit()
        print(f"Created Pilot Document: {doc.name}")
    else:
        print(f"Document already exists: {doc.name}")

    # 5. Create Pilot Requirements
    req1_id = uuid.UUID("55555555-5555-5555-5555-555555555551")
    req1 = db.query(Requirement).filter_by(id=req1_id).first()
    if not req1:
        req1 = Requirement(
            id=req1_id,
            project_id=project.id,
            source_document_id=doc.id,
            original_text=(
                "Sec-1: The contractor must encrypt all data at rest using AES-256."
            ),
            source_section="Sec-1",
            source_page=1,
            requirement_type="Security",
            mandatory=True,
            status="APPROVED",
            owner_name="Alice Pilot Reviewer",
        )
        db.add(req1)
        db.commit()
        print("Created Requirement 1 (APPROVED)")
    else:
        print("Requirement 1 already exists")

    req2_id = uuid.UUID("55555555-5555-5555-5555-555555555552")
    req2 = db.query(Requirement).filter_by(id=req2_id).first()
    if not req2:
        req2 = Requirement(
            id=req2_id,
            project_id=project.id,
            source_document_id=doc.id,
            original_text=(
                "Sec-2: Production observability logs must not contain "
                "plain passwords, secrets, or API keys."
            ),
            source_section="Sec-2",
            source_page=3,
            requirement_type="Security",
            mandatory=True,
            status="NEEDS_REVIEW",
            owner_name="Alice Pilot Reviewer",
        )
        db.add(req2)
        db.commit()
        print("Created Requirement 2 (NEEDS_REVIEW)")
    else:
        print("Requirement 2 already exists")

    # 6. Create Evidence Links
    ev_id = uuid.UUID("66666666-6666-6666-6666-666666666666")
    ev = db.query(EvidenceLink).filter_by(id=ev_id).first()
    if not ev:
        ev = EvidenceLink(
            id=ev_id,
            requirement_id=req2_id,
            document_id=doc.id,
            snippet=(
                "Production logs are scrubbed for sensitive keys like "
                "'password' and 'secret_key' via custom logging filters."
            ),
            page_number=3,
            score=0.95,
        )
        db.add(ev)
        db.commit()
        print("Created Evidence Link for Requirement 2")
    else:
        print("Evidence Link already exists")

    # 7. Create Draft Response
    resp_id = uuid.UUID("77777777-7777-7777-7777-777777777777")
    resp = db.query(DraftResponse).filter_by(id=resp_id).first()
    if not resp:
        resp = DraftResponse(
            id=resp_id,
            requirement_id=req2_id,
            content=(
                "RFP Architect MVP automatically sanitizes observability "
                "logs by masking sensitive credential patterns, "
                "preventing exposure."
            ),
            version=1,
            status="draft",
            confidence=0.98,
            needs_evidence=False,
        )
        db.add(resp)
        db.commit()
        print("Created Draft Response for Requirement 2")
    else:
        print("Draft Response already exists")

    print("Database seeding completed successfully!")

except Exception as e:
    db.rollback()
    print(f"Error during seeding: {e}")
    sys.exit(1)
finally:
    db.close()
