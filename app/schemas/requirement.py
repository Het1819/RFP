import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RequirementBase(BaseModel):
    original_text: str
    source_section: str | None = None
    source_page: int | None = None
    requirement_type: str | None = None
    mandatory: bool = False
    status: str = "NOT_STARTED"
    owner_name: str | None = None
    proposal_section: str | None = None
    risk_level: str | None = None


class RequirementCreate(RequirementBase):
    pass


class RequirementUpdate(RequirementBase):
    pass


class RequirementOut(RequirementBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_document_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
