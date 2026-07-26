import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposal_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_role: Mapped[str] = mapped_column(String(50), default="rfp", nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String(50), default="PENDING", nullable=False
    )
    version: Mapped[str | None] = mapped_column(
        String(50), default="1.0", nullable=True
    )
    review_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    ingestion_status: Mapped[str] = mapped_column(
        String(30),
        default="LEGACY_UNVERIFIED",
        server_default="LEGACY_UNVERIFIED",
        nullable=False,
    )  # A5b must set ingestion_status=QUARANTINED explicitly at every real upload
    # call site once quarantine storage exists; until then this honest default
    # avoids falsely marking unquarantined rows as quarantined.
    display_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_content_type: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    sha256_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scan_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scan_engine_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scan_signature_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    scan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scan_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    content_policy_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    content_policy_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    # Snapshot of sha256_digest/file_path captured at the moment a scan
    # attempt starts, so malware_scan.py can detect (and fail closed on) a
    # document whose quarantine file or digest changed between enqueue and
    # scan execution.
    scan_digest_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parser_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    operator_failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    promotion_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promotion_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promotion_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    clean_storage_identifier: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[Document] = relationship("Document", back_populates="pages")
