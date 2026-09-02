from __future__ import annotations

import asyncio

from arq import Retry
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.extractor import build_extractor, load_prompt
from app.models import Document, DocumentStatus
from app.queue import MemoryDocumentQueue
from app.storage import LocalDemoObjectStorage
from app.worker import process_document


async def recover_pending_documents(
    queue: MemoryDocumentQueue,
    storage: LocalDemoObjectStorage,
) -> int:
    """Reenfileira somente itens pendentes cujo objeto sobreviveu ao reinício local."""
    async with SessionLocal() as session:
        documents = list(
            (
                await session.scalars(
                    select(Document)
                    .where(Document.status == DocumentStatus.PENDING)
                    .order_by(Document.created_at)
                )
            ).all()
        )
    recovered = 0
    for document in documents:
        if document.object_key and await storage.exists(document.object_key):
            await queue.enqueue_outbox(f"recovery:{document.id}", document.id)
            recovered += 1
    return recovered


async def run_demo_worker(
    queue: MemoryDocumentQueue,
    storage: LocalDemoObjectStorage,
) -> None:
    """Consumidor local; nunca é criado quando DEMO_AUTOPROCESS está desativado."""
    extractor = build_extractor()
    prompt_version = getattr(extractor, "prompt_version", settings.prompt_version)
    context = {
        "storage": storage,
        "extractor": extractor,
        "prompt_version": prompt_version,
        "prompt": load_prompt(prompt_version),
    }
    while True:
        _event_id, document_id = await queue.dequeue()
        try:
            await process_document(context, document_id)
        except Retry:
            await asyncio.sleep(1)
            await queue.requeue(document_id)
        finally:
            queue.task_done()
