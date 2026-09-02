from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    PURGED = "PURGED"


class RunStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReviewDecision(str, enum.Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email_unique", "email", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole", native_enum=False, length=20, create_constraint=True),
        default=UserRole.OPERATOR,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_file_hash_unique", "file_hash", unique=True),
        Index("ix_documents_status_created", "status", "created_at"),
        Index("ix_documents_review_claim", "status", "claim_expires_at", "created_at"),
        Index("ix_documents_retention", "retention_until", "legal_hold", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename_original: Mapped[str] = mapped_column(String(255), nullable=False)
    filename_suggested: Mapped[str | None] = mapped_column(String(255))
    object_key: Mapped[str | None] = mapped_column(String(1024))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_channel: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)

    document_type: Mapped[str | None] = mapped_column(String(100))
    extracted_data: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=32),
        default=DocumentStatus.PENDING,
        index=True,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extractor_strategy: Mapped[str | None] = mapped_column(String(80))
    model_version: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(120))

    claimed_by: Mapped[str | None] = mapped_column(String(255))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    last_modified_by: Mapped[str | None] = mapped_column(String(255))

    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (Index("ix_extraction_runs_document_created", "document_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, native_enum=False, length=20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_document_occurred", "document_id", "occurred_at"),
        Index("ix_audit_actor_occurred", "actor_subject", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("documents.id", ondelete="SET NULL"))
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_roles: Mapped[list] = mapped_column(JSON_DOCUMENT, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_fields: Mapped[list | None] = mapped_column(JSON_DOCUMENT)
    details: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    source_ip_hash: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_eval_versions_created", "model_version", "prompt_version", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_version: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, native_enum=False, length=20), nullable=False)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
