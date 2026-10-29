"""A_Memorix 人物身份桥接。

人物数据存放在 FxBot 数据库的 ``chat_person`` 表（租户隔离）。A_Memorix 只需要
按 person_id 取别名、按姓名反查 person_id，因此这里直接读 SQLite，避免把异步
Session 传进同步算法。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ....utils.paths import database_path

_scope: ContextVar[str] = ContextVar("amemorix_person_scope", default="")


@dataclass(frozen=True)
class PersonRecord:
    person_id: str
    user_id: str = ""
    person_name: str = ""
    user_nickname: str = ""
    group_cardname: str = "[]"
    memory_points: str = "{}"


@contextmanager
def person_scope(tenant: str) -> Iterator[None]:
    """限定人物查询范围；A_Memorix 调用方不传租户时用它补齐隔离。"""
    token = _scope.set(str(tenant or "").strip())
    try:
        yield
    finally:
        _scope.reset(token)


def current_scope() -> str:
    return _scope.get()


def make_person_id(tenant: str, platform: str, user_id: str) -> str:
    raw = f"{tenant}:{platform}:{user_id}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def _connect() -> sqlite3.Connection | None:
    path = Path(database_path())
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None


def _has_person_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_person' LIMIT 1"
    ).fetchone()
    return row is not None


def resolve_person_identifier(identifier: str) -> str:
    """按 person_id、人名或群名片解析 person_id；查不到时返回空串。"""
    key = str(identifier or "").strip()
    if not key:
        return ""

    conn = _connect()
    if conn is not None:
        try:
            if not _has_person_table(conn):
                return _fallback(key)
            tenant = current_scope()
            where = ["person_id = ?"]
            params: list[str] = [key]
            if tenant:
                where = [f"tenant = ? AND ({' OR '.join(where)})"]
                params = [tenant, *params]
            row = conn.execute(f"SELECT person_id FROM chat_person WHERE {where[0]} LIMIT 1", params).fetchone()
            if row:
                return str(row[0])

            clauses = ["person_name = ?", "user_nickname = ?", "group_cardname LIKE ?"]
            params = [key, key, f"%{key}%"]
            prefix = "tenant = ? AND " if tenant else ""
            if tenant:
                params = [tenant, *params]
            row = conn.execute(
                f"SELECT person_id FROM chat_person WHERE {prefix}({' OR '.join(clauses)}) LIMIT 1",
                params,
            ).fetchone()
            if row:
                return str(row[0])
        except sqlite3.Error:
            return _fallback(key)
        finally:
            conn.close()

    return _fallback(key)


def _fallback(key: str) -> str:
    if len(key) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in key):
        return key.lower()
    return ""


def load_person_record(person_id: str) -> PersonRecord | None:
    token = str(person_id or "").strip()
    if not token:
        return None
    conn = _connect()
    if conn is None:
        return None
    try:
        if not _has_person_table(conn):
            return None
        tenant = current_scope()
        clauses = ["person_id = ?"]
        params: list[str] = [token]
        if tenant:
            clauses.insert(0, "tenant = ?")
            params.insert(0, tenant)
        row = conn.execute(
            "SELECT person_id, user_id, person_name, user_nickname, group_cardname, memory_points "
            f"FROM chat_person WHERE {' AND '.join(clauses)} LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return None
        return PersonRecord(
            person_id=str(row[0] or ""),
            user_id=str(row[1] or ""),
            person_name=str(row[2] or ""),
            user_nickname=str(row[3] or ""),
            group_cardname=str(row[4] or "[]"),
            memory_points=str(row[5] or "{}"),
        )
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def parse_group_nicks(raw_value: str) -> list[str]:
    try:
        items = json.loads(raw_value) if raw_value else []
    except (TypeError, ValueError):
        return []
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            value = str(item.get("group_cardname") or item.get("group_nick_name") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            names.append(value)
    return names
