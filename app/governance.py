from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import Principal
from app.models import AuditEvent, Document, DocumentStatus
from app.storage import ObjectStorage


def digest_value(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(
        settings.audit_hmac_key.encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def add_audit_event(
    session: AsyncSession,
    *,
    actor: Principal,
    action: str,
    document_id: str | None = None,
    changed_fields: list[str] | None = None,
    details: dict | None = None,
    source_ip: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            document_id=document_id,
            actor_subject=actor.subject,
            actor_roles=sorted(actor.roles),
            action=action,
            changed_fields=changed_fields,
            details=details,
            source_ip_hash=digest_value(source_ip),
        )
    )


async def purge_document(
    session: AsyncSession,
    storage: ObjectStorage,
    document: Document,
    *,
    actor: Principal,
    reason: str,
    source_ip: str | None = None,
) -> None:
    if document.legal_hold:
        raise ValueError("Documento sob legal hold não pode ser descartado.")
    if document.status == DocumentStatus.PURGED:
        return
    if document.object_key:
        await storage.delete(document.object_key)
    now = datetime.now(timezone.utc)
    document.object_key = None
    document.file_hash = None
    document.filename_original = "[DESCARTADO]"
    document.filename_suggested = None
    document.document_type = None
    document.extracted_data = None
    document.confidence_score = 0.0
    document.error_message = None
    document.claimed_by = None
    document.claim_expires_at = None
    document.status = DocumentStatus.PURGED
    document.purged_at = now
    document.updated_at = now
    document.last_modified_by = actor.subject
    add_audit_event(
        session,
        actor=actor,
        action="DOCUMENT_PURGED",
        document_id=document.id,
        changed_fields=[
            "object_key",
            "file_hash",
            "filename_original",
            "filename_suggested",
            "document_type",
            "extracted_data",
        ],
        details={"reason_digest": digest_value(reason), "policy": "retention-v1"},
        source_ip=source_ip,
    )


async def purge_expired_documents(session: AsyncSession, storage: ObjectStorage) -> int:
    now = datetime.now(timezone.utc)
    documents = list(
        (
            await session.scalars(
                select(Document)
                .where(
                    Document.retention_until <= now,
                    Document.legal_hold.is_(False),
                    Document.status != DocumentStatus.PURGED,
                )
                .order_by(Document.retention_until)
                .limit(200)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    system = Principal("system.retention", None, "Política de retenção", frozenset({"system"}))
    for document in documents:
        await purge_document(
            session,
            storage,
            document,
            actor=system,
            reason="Prazo de retenção expirado.",
        )
    await session.commit()
    return len(documents)

