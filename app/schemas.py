from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import DocumentStatus, ReviewDecision, RunStatus, UserRole


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_and_validate_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 255 or not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("E-mail inválido.")
    return normalized


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    role: UserRole


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_and_validate_email(value)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: str
    password: str = Field(min_length=5, max_length=72)
    role: UserRole = UserRole.OPERATOR

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_and_validate_email(value)

    @field_validator("password")
    @classmethod
    def bcrypt_size_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("A senha deve ter no máximo 72 bytes.")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class PrincipalRead(BaseModel):
    subject: str
    email: str | None
    name: str | None
    roles: list[str]
    role: UserRole | None = None


class PublicConfig(BaseModel):
    auth_disabled: bool
    auth_provider: str
    oidc_issuer: str
    oidc_client_id: str
    oidc_scopes: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename_original: str
    filename_suggested: str | None
    file_hash: str | None
    mime_type: str
    size_bytes: int
    source_channel: str
    document_type: str | None
    extracted_data: dict[str, Any] | None
    confidence_score: float
    status: DocumentStatus
    error_message: str | None
    attempt_count: int
    extractor_strategy: str | None
    model_version: str | None
    prompt_version: str | None
    claimed_by: str | None
    claim_expires_at: datetime | None
    retention_until: datetime
    legal_hold: bool
    purged_at: datetime | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class UploadResponse(BaseModel):
    document: DocumentRead
    duplicate: bool
    message: str


class DocumentList(BaseModel):
    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


class ClaimRequest(BaseModel):
    lease_seconds: int | None = Field(default=None, ge=60, le=3600)


class ReviewSubmit(BaseModel):
    decision: ReviewDecision = ReviewDecision.APPROVE
    document_type: str = Field(min_length=2, max_length=100)
    filename_suggested: str = Field(min_length=3, max_length=255)
    extracted_data: dict[str, Any]
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("filename_suggested")
    @classmethod
    def safe_suggested_filename(cls, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._() -]+", "_", Path(value).name).strip(" .")
        if not cleaned:
            raise ValueError("Nome sugerido inválido.")
        return cleaned


class RetentionUpdate(BaseModel):
    retention_until: datetime | None = None
    legal_hold: bool | None = None
    reason: str = Field(min_length=5, max_length=500)


class PurgeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str | None
    actor_subject: str
    actor_roles: list[str]
    action: str
    changed_fields: list[str] | None
    details: dict[str, Any] | None
    occurred_at: datetime


class EvaluationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_version: str
    model_version: str
    prompt_version: str
    strategy: str
    status: RunStatus
    total_cases: int
    metrics: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


class QueueSummary(BaseModel):
    waiting: int
    claimed: int


class HealthResponse(BaseModel):
    status: str
    service: str
    database: str
    queue: str
    storage: str
