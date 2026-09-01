from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BASE_DIR, settings
from app.database import close_db, create_test_schema, get_session, verify_database
from app.dependencies import (
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_REVIEW,
    ROLE_SUBMIT,
    Principal,
    get_current_principal,
    require_role,
)
from app.governance import add_audit_event, digest_value, purge_document
from app.models import AuditEvent, Document, DocumentStatus, EvaluationRun, OutboxEvent, ReviewDecision
from app.queue import DurableDocumentQueue, MemoryDocumentQueue
from app.schemas import (
    AuditEventRead,
    ClaimRequest,
    DocumentList,
    DocumentRead,
    EvaluationRunRead,
    HealthResponse,
    PrincipalRead,
    PublicConfig,
    PurgeRequest,
    QueueSummary,
    RetentionUpdate,
    ReviewSubmit,
    UploadResponse,
)
from app.storage import MemoryObjectStorage, S3ObjectStorage, make_object_key, receive_upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    if settings.testing:
        await create_test_schema()
        storage = MemoryObjectStorage()
        queue = MemoryDocumentQueue()
    else:
        await verify_database()
        storage = S3ObjectStorage()
        queue = DurableDocumentQueue()
    await storage.start()
    await queue.start()
    app.state.storage = storage
    app.state.queue = queue
    yield
    await queue.close()
    await close_db()


app = FastAPI(
    title="DOC Intelligence API",
    version="2.0.0",
    docs_url="/api/docs" if settings.environment != "production" else None,
    openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    lifespan=lifespan,
)
api = APIRouter(prefix="/api/v1")


def get_storage(request: Request):
    return request.app.state.storage


def get_queue(request: Request):
    return request.app.state.queue


def source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def publish_outbox(session: AsyncSession, queue, event: OutboxEvent) -> None:
    try:
        await queue.enqueue_outbox(event.id, event.aggregate_id)
        event.published_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception:
        # O evento permanece no outbox e o dispatcher do worker o publicará.
        await session.rollback()


async def delete_object_safely(storage, object_key: str | None) -> None:
    if not object_key:
        return
    try:
        await storage.delete(object_key)
    except Exception:
        # Reconciliação/lifecycle remove eventual órfão sem ocultar a falha original.
        return


@app.get("/api/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    return PublicConfig(
        auth_disabled=settings.auth_disabled,
        oidc_issuer=settings.oidc_issuer,
        oidc_client_id=settings.oidc_client_id,
        oidc_scopes=settings.oidc_scopes,
    )


@api.get("/me", response_model=PrincipalRead)
async def me(principal: Principal = Depends(get_current_principal)) -> PrincipalRead:
    return PrincipalRead(
        subject=principal.subject,
        email=principal.email,
        name=principal.name,
        roles=sorted(principal.roles),
    )


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    checks = {"database": "ok", "queue": "ok", "storage": "ok"}
    try:
        await verify_database()
    except Exception:
        checks["database"] = "error"
    try:
        await request.app.state.queue.healthcheck()
    except Exception:
        checks["queue"] = "error"
    try:
        await request.app.state.storage.healthcheck()
    except Exception:
        checks["storage"] = "error"
    return HealthResponse(
        status="ok" if all(value == "ok" for value in checks.values()) else "degraded",
        service=settings.app_name,
        **checks,
    )


@api.post("/documents", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    source_channel: str = Form(default="internal"),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_SUBMIT)),
    storage=Depends(get_storage),
    queue=Depends(get_queue),
) -> UploadResponse:
    channel = source_channel.strip().lower()
    if channel not in {"whatsapp", "email", "balcao", "internal"}:
        raise HTTPException(status_code=422, detail="Canal de origem inválido.")

    stored = await receive_upload(file)
    existing = await session.scalar(
        select(Document).where(
            Document.file_hash == stored.file_hash,
            Document.status != DocumentStatus.PURGED,
        )
    )
    if existing:
        stored.temp_path.unlink(missing_ok=True)
        add_audit_event(
            session,
            actor=principal,
            action="DUPLICATE_RECEIVED",
            document_id=existing.id,
            details={"source_channel": channel},
            source_ip=source_ip(request),
        )
        await session.commit()
        return UploadResponse(
            document=DocumentRead.model_validate(existing),
            duplicate=True,
            message="Arquivo já recebido; o processamento existente foi reutilizado.",
        )

    now = datetime.now(timezone.utc)
    document = Document(
        filename_original=stored.original_name,
        object_key=None,
        file_hash=stored.file_hash,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        source_channel=channel,
        status=DocumentStatus.PENDING,
        created_by=principal.subject,
        retention_until=now + timedelta(days=settings.retention_days),
    )
    event: OutboxEvent | None = None
    object_key: str | None = None
    try:
        session.add(document)
        await session.flush()
        object_key = make_object_key(document.id, stored.file_hash, stored.mime_type)
        document.object_key = object_key
        event = OutboxEvent(
            event_type="DOCUMENT_UPLOADED",
            aggregate_id=document.id,
            payload={"document_id": document.id},
        )
        session.add(event)
        add_audit_event(
            session,
            actor=principal,
            action="DOCUMENT_RECEIVED",
            document_id=document.id,
            changed_fields=["object_key", "file_hash", "retention_until"],
            details={"source_channel": channel, "size_bytes": stored.size_bytes},
            source_ip=source_ip(request),
        )
        await storage.put_file(object_key, stored.temp_path, stored.mime_type)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await delete_object_safely(storage, object_key)
        existing = await session.scalar(
            select(Document).where(
                Document.file_hash == stored.file_hash,
                Document.status != DocumentStatus.PURGED,
            )
        )
        if not existing:
            raise HTTPException(status_code=409, detail="Conflito ao registrar o documento.")
        return UploadResponse(
            document=DocumentRead.model_validate(existing),
            duplicate=True,
            message="Upload concorrente deduplicado por SHA-256.",
        )
    except Exception:
        await session.rollback()
        await delete_object_safely(storage, object_key)
        raise
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert event is not None
    await publish_outbox(session, queue, event)
    return UploadResponse(
        document=DocumentRead.model_validate(document),
        duplicate=False,
        message="Documento persistido com criptografia e publicado na fila durável.",
    )


@api.get("/documents", response_model=DocumentList)
async def list_documents(
    status_filter: DocumentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_READ)),
) -> DocumentList:
    predicate = Document.status == status_filter if status_filter else None
    count_query = select(func.count()).select_from(Document)
    items_query = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    if predicate is not None:
        count_query = count_query.where(predicate)
        items_query = items_query.where(predicate)
    total = int((await session.scalar(count_query)) or 0)
    items = list((await session.scalars(items_query)).all())
    return DocumentList(
        items=[DocumentRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@api.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_READ)),
) -> DocumentRead:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return DocumentRead.model_validate(document)


@api.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_READ)),
    storage=Depends(get_storage),
) -> StreamingResponse:
    document = await session.get(Document, document_id)
    if not document or not document.object_key or document.status == DocumentStatus.PURGED:
        raise HTTPException(status_code=404, detail="Conteúdo não encontrado.")
    content = await storage.get_bytes(document.object_key)
    filename = quote(document.filename_suggested or document.filename_original)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@api.post("/documents/{document_id}/retry", response_model=DocumentRead)
async def retry_document(
    request: Request,
    document_id: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
    queue=Depends(get_queue),
) -> DocumentRead:
    result = await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.status == DocumentStatus.FAILED)
        .values(
            status=DocumentStatus.PENDING,
            attempt_count=0,
            error_message=None,
            last_modified_by=principal.subject,
        )
        .returning(Document)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=409, detail="Somente documentos com falha podem ser reenfileirados.")
    event = OutboxEvent(
        event_type="DOCUMENT_RETRY_REQUESTED",
        aggregate_id=document_id,
        payload={"document_id": document_id},
    )
    session.add(event)
    add_audit_event(
        session,
        actor=principal,
        action="DOCUMENT_RETRY_REQUESTED",
        document_id=document_id,
        changed_fields=["status", "attempt_count"],
        source_ip=source_ip(request),
    )
    await session.commit()
    await publish_outbox(session, queue, event)
    return DocumentRead.model_validate(document)


@api.get("/review/summary", response_model=QueueSummary)
async def review_summary(
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_REVIEW)),
) -> QueueSummary:
    now = datetime.now(timezone.utc)
    waiting = await session.scalar(
        select(func.count()).select_from(Document).where(
            Document.status == DocumentStatus.NEEDS_REVIEW,
            or_(Document.claimed_by.is_(None), Document.claim_expires_at < now),
        )
    )
    claimed = await session.scalar(
        select(func.count()).select_from(Document).where(
            Document.status == DocumentStatus.NEEDS_REVIEW,
            Document.claimed_by.is_not(None),
            Document.claim_expires_at >= now,
        )
    )
    return QueueSummary(waiting=int(waiting or 0), claimed=int(claimed or 0))


@api.post("/review/claim", response_model=DocumentRead | None)
async def claim_review(
    request: Request,
    body: ClaimRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_REVIEW)),
):
    now = datetime.now(timezone.utc)
    existing = await session.scalar(
        select(Document)
        .where(
            Document.status == DocumentStatus.NEEDS_REVIEW,
            Document.claimed_by == principal.subject,
            Document.claim_expires_at >= now,
        )
        .order_by(Document.created_at)
    )
    if existing:
        return DocumentRead.model_validate(existing)

    lease = timedelta(seconds=body.lease_seconds or settings.review_lease_seconds)
    candidate = (
        select(Document.id)
        .where(
            Document.status == DocumentStatus.NEEDS_REVIEW,
            or_(Document.claimed_by.is_(None), Document.claim_expires_at < now),
        )
        .order_by(Document.created_at)
        .limit(1)
        .scalar_subquery()
    )
    result = await session.execute(
        update(Document)
        .where(
            Document.id == candidate,
            Document.status == DocumentStatus.NEEDS_REVIEW,
            or_(Document.claimed_by.is_(None), Document.claim_expires_at < now),
        )
        .values(
            claimed_by=principal.subject,
            claim_expires_at=now + lease,
            last_modified_by=principal.subject,
            updated_at=now,
        )
        .returning(Document)
    )
    document = result.scalar_one_or_none()
    if document:
        add_audit_event(
            session,
            actor=principal,
            action="REVIEW_CLAIMED",
            document_id=document.id,
            changed_fields=["claimed_by", "claim_expires_at"],
            source_ip=source_ip(request),
        )
    await session.commit()
    return DocumentRead.model_validate(document) if document else None


@api.post("/review/{document_id}/heartbeat", response_model=DocumentRead)
async def heartbeat_review(
    document_id: str,
    body: ClaimRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_REVIEW)),
) -> DocumentRead:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.status == DocumentStatus.NEEDS_REVIEW,
            Document.claimed_by == principal.subject,
            Document.claim_expires_at >= now,
        )
        .values(claim_expires_at=now + timedelta(seconds=body.lease_seconds or settings.review_lease_seconds))
        .returning(Document)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=409, detail="A reserva expirou ou pertence a outro operador.")
    await session.commit()
    return DocumentRead.model_validate(document)


@api.post("/review/{document_id}/submit", response_model=DocumentRead)
async def submit_review(
    request: Request,
    document_id: str,
    body: ReviewSubmit,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_REVIEW)),
) -> DocumentRead:
    now = datetime.now(timezone.utc)
    before = await session.get(Document, document_id)
    if not before:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    old_data = before.extracted_data or {}
    changed_fields = sorted(
        key
        for key in set(old_data) | set(body.extracted_data)
        if old_data.get(key) != body.extracted_data.get(key)
    )
    target_status = (
        DocumentStatus.COMPLETED
        if body.decision == ReviewDecision.APPROVE
        else DocumentStatus.FAILED
    )
    result = await session.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.status == DocumentStatus.NEEDS_REVIEW,
            Document.claimed_by == principal.subject,
            Document.claim_expires_at >= now,
        )
        .values(
            document_type=body.document_type.strip(),
            filename_suggested=body.filename_suggested.strip(),
            extracted_data=body.extracted_data,
            status=target_status,
            error_message="Rejeitado na conferência humana." if body.decision == ReviewDecision.REJECT else None,
            claimed_by=None,
            claim_expires_at=None,
            last_modified_by=principal.subject,
            updated_at=now,
        )
        .returning(Document)
        .execution_options(synchronize_session=False)
    )
    document = result.scalar_one_or_none()
    if not document:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A reserva expirou ou pertence a outro operador.")
    add_audit_event(
        session,
        actor=principal,
        action="REVIEW_SUBMITTED",
        document_id=document_id,
        changed_fields=["extracted_data." + field for field in changed_fields]
        + ["document_type", "filename_suggested", "status"],
        details={
            "decision": body.decision.value,
            "notes_digest": digest_value(body.notes),
        },
        source_ip=source_ip(request),
    )
    await session.commit()
    await session.refresh(document)
    return DocumentRead.model_validate(document)


@api.patch("/documents/{document_id}/retention", response_model=DocumentRead)
async def update_retention(
    request: Request,
    document_id: str,
    body: RetentionUpdate,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> DocumentRead:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    changed = []
    if body.retention_until is not None:
        document.retention_until = body.retention_until
        changed.append("retention_until")
    if body.legal_hold is not None:
        document.legal_hold = body.legal_hold
        changed.append("legal_hold")
    document.last_modified_by = principal.subject
    add_audit_event(
        session,
        actor=principal,
        action="RETENTION_UPDATED",
        document_id=document.id,
        changed_fields=changed,
        details={"reason_digest": digest_value(body.reason)},
        source_ip=source_ip(request),
    )
    await session.commit()
    return DocumentRead.model_validate(document)


@api.post("/documents/{document_id}/purge", response_model=DocumentRead)
async def purge_now(
    request: Request,
    document_id: str,
    body: PurgeRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
    storage=Depends(get_storage),
) -> DocumentRead:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    try:
        await purge_document(
            session,
            storage,
            document,
            actor=principal,
            reason=body.reason,
            source_ip=source_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return DocumentRead.model_validate(document)


@api.get("/audit", response_model=list[AuditEventRead])
async def list_audit_events(
    document_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> list[AuditEventRead]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if document_id:
        query = query.where(AuditEvent.document_id == document_id)
    events = list((await session.scalars(query)).all())
    return [AuditEventRead.model_validate(event) for event in events]


@api.get("/evaluations", response_model=list[EvaluationRunRead])
async def list_evaluation_runs(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> list[EvaluationRunRead]:
    runs = list(
        (
            await session.scalars(
                select(EvaluationRun)
                .order_by(EvaluationRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return [EvaluationRunRead.model_validate(run) for run in runs]


app.include_router(api)


@app.get("/", include_in_schema=False)
async def spa_index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html", media_type="text/html")
