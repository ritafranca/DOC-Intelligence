from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


engine_options: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("postgresql"):
    engine_options.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=1800,
    )
else:
    engine_options["connect_args"] = {"timeout": 30}

engine = create_async_engine(settings.database_url, **engine_options)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_test_schema() -> None:
    if not settings.testing:
        raise RuntimeError("create_all é permitido somente em testes; use Alembic no runtime.")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def verify_database() -> None:
    async with engine.connect() as connection:
        await connection.exec_driver_sql("SELECT 1")


async def close_db() -> None:
    await engine.dispose()


async def get_session():
    async with SessionLocal() as session:
        yield session

