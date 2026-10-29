"""chat_* 表结构与索引迁移。"""

from __future__ import annotations

from sqlalchemy import Index, Table, Column, text
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from ...db import base_models
from .models import (
    ChatTurn,
    ChatEmoji,
    ChatStyle,
    ChatTrace,
    ChatJargon,
    ChatMessage,
    ChatTurnItem,
    ChatJobCursor,
    ChatExpression,
    ChatSessionState,
    ChatPlannerSample,
    ChatRecallTombstone,
)

_MODELS = (
    ChatMessage,
    ChatRecallTombstone,
    ChatTurn,
    ChatTurnItem,
    ChatSessionState,
    ChatJobCursor,
    ChatStyle,
    ChatJargon,
    ChatExpression,
    ChatPlannerSample,
    ChatEmoji,
    ChatTrace,
)
_DIALECT = sqlite.dialect()


def _create_table_sql(table: Table) -> str:
    ddl = str(CreateTable(table).compile(dialect=_DIALECT)).strip()
    return ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)


def _create_index_sql(index: Index) -> str:
    ddl = str(CreateIndex(index).compile(dialect=_DIALECT)).strip()
    unique = ddl.startswith("CREATE UNIQUE INDEX ")
    prefix = "CREATE UNIQUE INDEX IF NOT EXISTS " if unique else "CREATE INDEX IF NOT EXISTS "
    return ddl.replace("CREATE UNIQUE INDEX " if unique else "CREATE INDEX ", prefix, 1)


def _default_literal(column: Column) -> str | None:
    default = column.default
    if default is None or not default.is_scalar:
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _add_column_sql(column: Column) -> str:
    type_sql = column.type.compile(dialect=_DIALECT)
    statement = f'ALTER TABLE "{column.table.name}" ADD COLUMN "{column.name}" {type_sql}'
    if column.nullable:
        return statement
    literal = _default_literal(column)
    if literal is None:
        raise RuntimeError(f"缺少非空列且无默认值: {column.table.name}.{column.name}")
    return f"{statement} NOT NULL DEFAULT {literal}"


async def _table_names(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
    return {row[0] for row in result}


async def _index_names(conn: AsyncConnection, table: str) -> set[str]:
    result = await conn.execute(text(f'PRAGMA index_list("{table}")'))
    return {row[1] for row in result}


async def _ensure_columns(conn: AsyncConnection, table: Table) -> None:
    result = await conn.execute(text(f'PRAGMA table_info("{table.name}")'))
    existing = {row[1] for row in result}
    for column in table.columns:
        if column.name not in existing:
            await conn.execute(text(_add_column_sql(column)))


async def _ensure_indexes(conn: AsyncConnection, table: Table) -> None:
    existing = await _index_names(conn, table.name)
    for index in table.indexes:
        if index.name not in existing:
            await conn.execute(text(_create_index_sql(index)))


async def ensure_schema(bind: AsyncEngine | None = None) -> None:
    """补齐缺失的表、列和索引；无法修复时抛出，不吞异常。"""
    target = bind or base_models.engine
    if target is None:
        raise RuntimeError("数据库尚未初始化")

    async with target.begin() as conn:
        tables = await _table_names(conn)
        for model in _MODELS:
            table = model.__table__
            if table.name not in tables:
                await conn.execute(text(_create_table_sql(table)))
            await _ensure_columns(conn, table)
            await _ensure_indexes(conn, table)
