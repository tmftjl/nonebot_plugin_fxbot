"""群管身份判断工具。"""

from __future__ import annotations

from nonebot import get_driver


def superusers() -> set[str]:
    """读取 NoneBot 主人列表。"""
    try:
        return {str(item) for item in get_driver().config.superusers or []}
    except Exception:
        return set()


def is_superuser_id(user_id: str | int | None) -> bool:
    """判断指定用户是否为主人。"""
    return user_id is not None and str(user_id) in superusers()
