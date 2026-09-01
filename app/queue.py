from __future__ import annotations

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

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> None:
        return None

    async def enqueue_outbox(self, event_id: str, document_id: str) -> None:
        self.enqueued.append((event_id, document_id))

