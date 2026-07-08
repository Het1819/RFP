import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PilotFeedback(Base):
    __tablename__ = "pilot_feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proposal_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # BUG, USABILITY, AI_QUALITY, EVIDENCE, EXPORT, PERFORMANCE, OTHER
    severity: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # LOW, MEDIUM, HIGH, BLOCKER
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="OPEN", nullable=False
    )  # OPEN, TRIAGED, CLOSED
