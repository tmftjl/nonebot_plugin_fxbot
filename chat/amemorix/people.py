"""人物身份维护。

每个租户单独保存一份人物记录；``person_id`` 与 ``_host.person_db`` 使用同一算法，
A_Memorix 画像服务按这个 ID 读写。
"""

from __future__ import annotations

import json
from typing import Any

from nonebot import logger
from sqlmodel import select

from ...db import base_models
from ...utils.tz import now, to_naive_utc
from ._host.person_db import make_person_id, person_scope
from .models import ChatPerson


def session_parts(tenant: str) -> tuple[str, str]:
    """从租户串取出平台与群号/用户号。"""
    token = str(tenant or "").strip()
    if ":group:" in token:
        platform, _, group_id = token.partition(":group:")
        return platform.split(":", 1)[0], group_id
    if ":private:" in token:
        platform, _, user_id = token.partition(":private:")
        return platform.split(":", 1)[0], user_id
    return token.split(":", 1)[0], ""


def _group_cards(raw: str) -> list[dict[str, str]]:
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _merge_group_card(raw: str, group_id: str, card: str) -> str:
    if not group_id or not card:
        return raw or "[]"
    cards = _group_cards(raw)
    for item in cards:
        if str(item.get("group_id") or "") == group_id:
            item["group_cardname"] = card
            break
    else:
        cards.append({"group_id": group_id, "group_cardname": card})
    return json.dumps(cards[-20:], ensure_ascii=False)


async def remember_person(
    tenant: str,
    platform: str,
    user_id: str,
    display_name: str,
    *,
    group_id: str = "",
    group_cardname: str = "",
) -> str:
    """写入或更新人物；返回 person_id，数据库不可用时返回空串。"""
    tenant = str(tenant or "").strip()
    user_id = str(user_id or "").strip()
    if not tenant or not user_id:
        return ""
    platform = str(platform or "").strip() or session_parts(tenant)[0]
    person_id = make_person_id(tenant, platform, user_id)
    name = " ".join(str(display_name or "").split())[:64] or user_id
    if base_models.async_maker is None:
        return ""
    try:
        async with base_models.async_maker() as session:
            row = (
                await session.execute(
                    select(ChatPerson).where(ChatPerson.tenant == tenant, ChatPerson.person_id == person_id)
                )
            ).scalars().first()
            if row is None:
                row = ChatPerson(
                    tenant=tenant,
                    person_id=person_id,
                    platform=platform,
                    user_id=user_id,
                    person_name=name,
                    user_nickname=name,
                )
                session.add(row)
            else:
                row.user_nickname = name
                if not row.person_name:
                    row.person_name = name
            row.group_cardname = _merge_group_card(row.group_cardname, group_id, group_cardname)
            row.updated_at = to_naive_utc(now())
            await session.commit()
    except Exception:
        logger.opt(exception=True).debug("[A_Memorix] 人物身份写入失败")
        return ""
    return person_id


async def person_name(tenant: str, person_id: str) -> str:
    """取人物主称呼；查不到时返回空串。"""
    if base_models.async_maker is None:
        return ""
    try:
        async with base_models.async_maker() as session:
            row = (
                await session.execute(
                    select(ChatPerson).where(ChatPerson.tenant == tenant, ChatPerson.person_id == person_id)
                )
            ).scalars().first()
    except Exception:
        return ""
    return str(getattr(row, "person_name", "") or "")


def scoped(tenant: str):
    """A_Memorix 同步读写人物库时限定租户。"""
    return person_scope(tenant)


__all__ = ["ChatPerson", "person_name", "remember_person", "scoped", "session_parts"]
