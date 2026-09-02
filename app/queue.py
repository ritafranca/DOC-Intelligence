from __future__ import annotations

import asyncio
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


class DocumentQueue(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def healthcheck(self) -> None: ...
    async def enqueue_outbox(self, event_id: str, document_id: str) -> None: ...


class DurableDocumentQueue:
    def __init__(self) -> None:
        self.redis: ArqRedis | None = None

    async def start(self) -> None:
        self.redis = await create_pool(
            RedisSettings.from_dsn(settings.redis_url),
            default_queue_name=settings.queue_name,
        )

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()
            self.redis = None

    async def healthcheck(self) -> None:
        if not self.redis:
            raise RuntimeError("Fila não inicializada.")
        await self.redis.ping()

    async def enqueue_outbox(self, event_id: str, document_id: str) -> None:
        if not self.redis:
            raise RuntimeError("Fila não inicializada.")
        await self.redis.enqueue_job(
            "process_document",
            document_id,
            _job_id=f"outbox:{event_id}",
            _queue_name=settings.queue_name,
        )


class MemoryDocumentQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self._pending: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> None:
        if self._consumer_task is not None and self._consumer_task.done():
            raise RuntimeError("Consumidor da fila de demonstração não está ativo.")
        return None

    async def enqueue_outbox(self, event_id: str, document_id: str) -> None:
        self.enqueued.append((event_id, document_id))
        await self._pending.put((event_id, document_id))

    async def dequeue(self) -> tuple[str, str]:
        return await self._pending.get()

    def task_done(self) -> None:
        self._pending.task_done()

    async def requeue(self, document_id: str) -> None:
        await self._pending.put((f"retry:{document_id}", document_id))

    def set_consumer_task(self, task: asyncio.Task) -> None:
        self._consumer_task = task
