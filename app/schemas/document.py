import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentBase(BaseModel):
    name: str
    file_type: str
    doc_role: str = "rfp"


class DocumentOut(DocumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    file_path: str
    processing_status: str
    created_at: datetime
    updated_at: datetime
