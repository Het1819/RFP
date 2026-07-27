import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Narrow, organization-scoped capability to review extracted requirement
    # candidates and promote them to authoritative Requirements. Deliberately a
    # single boolean rather than a role enum or RBAC framework: the only
    # authority decision the MVP needs is "may this human approve machine
    # output?". Defaults to false everywhere (Python default, server default,
    # and migration backfill) so the capability is never granted implicitly --
    # not to existing users, project creators, or the first user in an org.
    #
    # MVP limitation: the capability is org-wide. A reviewer may review
    # candidates for any project inside their own organization. Project-scoped
    # grants can be layered on later if customer evidence requires them.
    can_review_requirements: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
