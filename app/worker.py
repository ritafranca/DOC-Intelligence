from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import select, update

from app.config import settings
from app.database import SessionLocal, close_db, verify_database
from app.extractor import build_extractor, load_prompt
from app.governance import purge_expired_documents
from app.models import Document, DocumentStatus, ExtractionRun, OutboxEvent, RunStatus
from app.storage import ALLOWED_MIME_TYPES, S3ObjectStorage


async def startup(ctx: dict) -> None:
    settings.validate_runtime()
    await verify_database()
    storage = S3ObjectStorage()
    await storage.start()
    ctx["storage"] = storage
    ctx["extractor"] = build_extractor()
    ctx["prompt"] = load_prompt(settings.prompt_version)


async def shutdown(_ctx: dict) -> None:
    await close_db()


async def process_document(ctx: dict, document_id: str) -> None:
    now = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        claimed = await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == DocumentStatus.PENDING)
            .values(
                status=DocumentStatus.PROCESSING,
                attempt_count=Document.attempt_count + 1,
                error_message=None,
                updated_at=now,
            )
            .returning(Document.attempt_count)
        )
        attempt = claimed.scalar_one_or_none()
        if attempt is None:
            await session.rollback()
            return
        document = await session.get(Document, document_id)
        if not document or not document.object_key:
            await session.rollback()
            return
        object_key = document.object_key
        mime_type = document.mime_type
        original_filename = document.filename_original
        session.add(
            ExtractionRun(
                id=run_id,
                document_id=document_id,
                attempt_number=attempt,
                strategy=ctx["extractor"].strategy_name,
                model_version=ctx["extractor"].model_version,
                prompt_version=settings.prompt_version,
                status=RunStatus.STARTED,
            )
        )
        await session.commit()

    suffix = ALLOWED_MIME_TYPES[mime_type]
    temp_path = settings.temp_dir / f"worker-{uuid.uuid4()}{suffix}"
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        await ctx["storage"].download_file(object_key, temp_path)
        result = await ctx["extractor"].extract(
            document_id=document_id,
            file_path=temp_path,
            mime_type=mime_type,
            original_filename=original_filename,
            prompt=ctx["prompt"],
        )
        finished = datetime.now(timezone.utc)
        final_status = (
            DocumentStatus.COMPLETED
            if result.confidence_score >= settings.review_threshold
            else DocumentStatus.NEEDS_REVIEW
        )
        async with SessionLocal() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id, Document.status == DocumentStatus.PROCESSING)
                .values(
                    document_type=result.document_type,
                    extracted_data=result.extracted_data,
                    confidence_score=result.confidence_score,
                    filename_suggested=result.filename_suggested,
                    status=final_status,
                    extractor_strategy=ctx["extractor"].strategy_name,
                    model_version=ctx["extractor"].model_version,
                    prompt_version=settings.prompt_version,
                    processed_at=finished,
                    updated_at=finished,
                )
            )
            await session.execute(
                update(ExtractionRun)
                .where(ExtractionRun.id == run_id)
                .values(
                    status=RunStatus.SUCCEEDED,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    finished_at=finished,
                )
            )
            await session.commit()
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        safe_error = f"{type(exc).__name__}: falha temporária no extrator"[:500]
        retry = attempt < settings.max_processing_attempts
        async with SessionLocal() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id, Document.status == DocumentStatus.PROCESSING)
                .values(
                    status=DocumentStatus.PENDING if retry else DocumentStatus.FAILED,
                    error_message=safe_error,
                    updated_at=finished,
                )
            )
            await session.execute(
                update(ExtractionRun)
                .where(ExtractionRun.id == run_id)
                .values(
                    status=RunStatus.FAILED,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_message=safe_error,
                    finished_at=finished,
                )
            )
            await session.commit()
        if retry:
            raise Retry(defer=min(2**attempt, 30)) from exc
    finally:
        temp_path.unlink(missing_ok=True)


async def dispatch_outbox(ctx: dict) -> int:
    async with SessionLocal() as session:
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        published = 0
        for event in events:
            event.attempts += 1
            try:
                await ctx["redis"].enqueue_job(
                    "process_document",
                    event.aggregate_id,
                    _job_id=f"outbox:{event.id}",
                    _queue_name=settings.queue_name,
                )
                event.published_at = datetime.now(timezone.utc)
                published += 1
            except Exception:
                continue
        await session.commit()
        return published


async def run_retention_job(ctx: dict) -> int:
    async with SessionLocal() as session:
        return await purge_expired_documents(session, ctx["storage"])


class WorkerSettings:
    functions = [process_document]
    cron_jobs = [
        cron(dispatch_outbox, second=set(range(0, 60, 10)), run_at_startup=True),
        cron(run_retention_job, hour=3, minute=15),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.queue_name
    max_jobs = settings.worker_concurrency
    job_timeout = int(settings.provider_timeout_seconds) + 30
    max_tries = settings.max_processing_attempts
    keep_result = 3600

