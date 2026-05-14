"""数据库基础层，按 temp/db/base_models.py 思路适配。"""

from __future__ import annotations

import asyncio
import sqlite3
from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, TypeVar

from nonebot import logger
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import Field, SQLModel, select

from ..utils.paths import database_path

T = TypeVar("T", bound="BaseIDModel")
P = TypeVar("P")
R = TypeVar("R")

DB_URL = f"sqlite+aiosqlite:///{database_path()}"

engine: AsyncEngine | None = None
async_maker: async_sessionmaker[AsyncSession] | None = None
sqlite_semaphore: asyncio.Semaphore | None = None

_init_lock = asyncio.Lock()
_initialized = False


async def init_database() -> None:
    global async_maker, engine, sqlite_semaphore, _initialized
    if _initialized:
        return

    async with _init_lock:
        if _initialized:
            return

        db_path = database_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            pool_recycle=1800,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng.sync_engine, "connect")
        def _set_pragmas(dbapi_connection: sqlite3.Connection, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        engine = eng
        sqlite_semaphore = asyncio.Semaphore(20)
        async_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        _initialized = True
        logger.info(f"[FxBot] Database initialized: {db_path}")


def is_initialized() -> bool:
    return _initialized


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    if async_maker is None:
        raise RuntimeError("Database is not initialized")
    return async_maker


def with_session(func: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> R:
        session = kwargs.pop("session", None)
        if session is not None:
            return await func(*args, session=session, **kwargs)
        maker = get_session_maker()
        async with maker() as new_session:
            result = await func(*args, session=new_session, **kwargs)
            await new_session.commit()
            return result

    return wrapper


class BaseIDModel(SQLModel):
    id: int | None = Field(default=None, primary_key=True)

    @classmethod
    @with_session
    async def get_by_id(cls: type[T], id_: int, *, session: AsyncSession) -> T | None:
        return await session.get(cls, id_)

    @classmethod
    @with_session
    async def get_by_ids(cls: type[T], ids: list[int], *, session: AsyncSession) -> list[T]:
        if not ids:
            return []
        result = await session.execute(select(cls).where(cls.id.in_(ids)))  # type: ignore[attr-defined]
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def select_rows(cls: type[T], *, session: AsyncSession, **conditions: Any) -> list[T]:
        stmt = select(cls)
        for key, value in conditions.items():
            stmt = stmt.where(getattr(cls, key) == value)
        result = await session.execute(stmt)
        return list(result.scalars().all())
