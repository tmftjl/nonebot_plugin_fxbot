"""数据库基础层。"""

from __future__ import annotations

import asyncio
import sqlite3
from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, ParamSpec, TypeVar

from nonebot import logger
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel, select

from ..utils.paths import database_path

T = TypeVar("T", bound="BaseIDModel")
P = ParamSpec("P")
R = TypeVar("R")

DB_PATH = database_path()
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine: AsyncEngine | None = None
async_maker: async_sessionmaker[AsyncSession] | None = None
sqlite_semaphore: asyncio.Semaphore | None = None

_db_init_lock = asyncio.Lock()
_db_initialized = False

# 全局迁移语句列表，其他模块可以往里追加 SQL
exec_list: list[str] = []


async def init_database() -> None:
    """初始化 SQLite 数据库并创建表结构。"""
    global _db_initialized, engine, async_maker, sqlite_semaphore
    if _db_initialized:
        return

    async with _db_init_lock:
        if _db_initialized:
            return

        logger.info("[DB] Initializing SQLite database...")
        try:
            eng = create_async_engine(
                DB_URL,
                echo=False,
                pool_recycle=1800,
                connect_args={"check_same_thread": False},
            )

            @event.listens_for(eng.sync_engine, "connect")
            def _set_pragmas(
                dbapi_connection: sqlite3.Connection,
                _connection_record: Any,
            ) -> None:
                cur = dbapi_connection.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.execute("PRAGMA cache_size=20000")
                cur.execute("PRAGMA temp_store=MEMORY")
                cur.execute("PRAGMA mmap_size=134217728")
                cur.execute("PRAGMA optimize")
                cur.close()

            engine = eng
            sqlite_semaphore = asyncio.Semaphore(20)
            async_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

            if exec_list:
                logger.info(f"[DB] 执行 {len(exec_list)} 条迁移语句...")
                async with engine.begin() as conn:
                    from sqlalchemy import text

                    for sql in exec_list:
                        try:
                            await conn.execute(text(sql))
                            logger.debug(f"[DB] 迁移成功: {sql[:50]}...")
                        except Exception as e:
                            logger.debug(f"[DB] 迁移跳过 (可能已存在): {sql[:50]}... | {e}")

            _db_initialized = True
            logger.info("[DB] SQLite initialized successfully")
        except Exception as e:
            logger.exception(f"[DB] Initialization failed: {e}")
            raise ValueError("[DB] Initialization failed, please check environment and dependencies")


def is_initialized() -> bool:
    """数据库是否已初始化。"""
    return _db_initialized


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂。"""
    if async_maker is None:
        raise RuntimeError("Database is not initialized")
    return async_maker


def with_session(
    func: Callable[Concatenate[Any, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[Any, P], Awaitable[R]]:
    """为方法注入 AsyncSession。"""

    @wraps(func)
    async def wrapper(self, *args: P.args, **kwargs: P.kwargs):
        if not _db_initialized:
            raise RuntimeError("数据库尚未初始化，请先调用 init_database()")

        session = kwargs.pop("session", None)
        if session is not None:
            return await func(self, session, *args, **kwargs)

        async with async_maker() as new_session:  # type: ignore[operator]
            result = await func(self, new_session, *args, **kwargs)
            await new_session.commit()
            return result

    return wrapper


class BaseIDModel(SQLModel):
    """带自增主键的基础模型。"""

    id: int | None = Field(default=None, primary_key=True, title="id")

    @classmethod
    @with_session
    async def get_by_ids(
        cls: type[T],
        session: AsyncSession,
        ids: list[int],
    ) -> list[T]:
        """按主键批量查询。"""
        if not ids:
            return []
        stmt = select(cls).where(cls.id.in_(ids))  # type: ignore[attr-defined]
        result = await session.execute(stmt)
        return result.scalars().all()

    @classmethod
    @with_session
    async def select_rows(
        cls: type[T],
        session: AsyncSession,
        **conditions: Any,
    ) -> list[T]:
        """按等值条件查询多行。"""
        stmt = select(cls)
        if conditions:
            stmt = stmt.where(*[getattr(cls, k) == v for k, v in conditions.items()])
        result = await session.execute(stmt)
        return result.scalars().all()

    @classmethod
    @with_session
    async def _batch_insert_or_update(
        cls: type[T],
        session: AsyncSession,
        datas: list[dict[str, Any]],
        update_keys: list[str],
        index_elements: list[str],
    ) -> None:
        """批量插入或更新，仅实现 SQLite 版本。"""
        if not datas:
            return

        from sqlalchemy.dialects.sqlite import insert

        stmt = insert(cls).values(datas)
        update_stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={k: stmt.excluded[k] for k in update_keys},
        )
        await session.execute(update_stmt)
