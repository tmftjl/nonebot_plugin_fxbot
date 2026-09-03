"""统一平台 Bot 外观（Facade）。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .registry import get_platform_adapter


class PlatformBot:
    """面向业务层的统一 Bot 对象。"""

    def __init__(self, bot: Any):
        self.raw = bot
        self.adapter = get_platform_adapter(bot)

    def __getattr__(self, name: str):
        if name in {"raw", "adapter"}:
            raise AttributeError(name)
        return getattr(self.adapter, name)

    async def send_group_message(self, group_id: str, message: Any):
        return await self.adapter.send_group_message(self.raw, str(group_id), message)

    async def send_private_message(self, user_id: str, message: Any):
        return await self.adapter.send_private_message(self.raw, str(user_id), message)

    async def delete_message(self, message_id: int):
        return await self.adapter.delete_message(self.raw, message_id)

    async def get_message(self, message_id: int):
        return await self.adapter.get_message(self.raw, message_id)

    async def get_group_info(self, group_id: str):
        return await self.adapter.get_group_info(self.raw, str(group_id))

    async def get_group_member(self, group_id: str, user_id: str):
        return await self.adapter.get_group_member(self.raw, str(group_id), str(user_id))

    async def get_group_members(self, group_id: str):
        return await self.adapter.get_group_members(self.raw, str(group_id))

    async def get_group_list(self):
        return await self.adapter.get_group_list(self.raw)

    async def get_user(self, user_id: str):
        return await self.adapter.get_user(self.raw, str(user_id))

    def user_avatar(self, user_id: str):
        return self.adapter.user_avatar(self.raw, str(user_id))

    def group_avatar(self, group_id: str):
        return self.adapter.group_avatar(self.raw, str(group_id))

    async def ban(self, group_id: str, user_id: str, duration: int):
        return await self.adapter.ban(self.raw, str(group_id), str(user_id), duration)

    async def kick(self, group_id: str, user_id: str, reject_add_request: bool = False):
        return await self.adapter.kick(self.raw, str(group_id), str(user_id), reject_add_request)

    async def whole_ban(self, group_id: str, enable: bool):
        return await self.adapter.whole_ban(self.raw, str(group_id), enable)

    async def set_admin(self, group_id: str, user_id: str, enable: bool):
        return await self.adapter.set_admin(self.raw, str(group_id), str(user_id), enable)

    async def leave_group(self, group_id: str):
        return await self.adapter.leave_group(self.raw, str(group_id))

    async def set_special_title(self, group_id: str, user_id: str, title: str):
        return await self.adapter.set_special_title(self.raw, str(group_id), str(user_id), title)

    async def like(self, user_id: str, times: int = 1):
        return await self.adapter.like(self.raw, str(user_id), times)

    async def upload_file(self, **kwargs: Any):
        return await self.adapter.upload_file(self.raw, **kwargs)

    async def set_essence(self, message_id: int, enable: bool):
        return await self.adapter.set_essence(self.raw, message_id, enable)

    def mention_targets(self, message: Any, ignored_targets: set[str] | None = None):
        return self.adapter.mention_targets(message, ignored_targets)

    def first_mention_target(self, message: Any, ignored_targets: set[str] | None = None):
        values = self.mention_targets(message, ignored_targets)
        return values[0] if values else None


def platform_bot(bot: Any) -> PlatformBot:
    return PlatformBot(bot)


_current: ContextVar[PlatformBot | None] = ContextVar("fxbot_platform_bot", default=None)


def bind_bot(bot: Any) -> PlatformBot:
    client = PlatformBot(bot)
    _current.set(client)
    return client


def current_bot() -> PlatformBot:
    client = _current.get()
    if client is not None:
        return client
    try:
        from nonebot import get_bots

        bots = list(get_bots().values())
    except Exception:
        bots = []
    if len(bots) == 1:
        return PlatformBot(bots[0])
    raise RuntimeError("当前上下文无法唯一确定 Bot")


class _SelfBot:
    """当前事件 Bot 代理，业务层直接使用 selfBot。"""

    def _client(self) -> PlatformBot:
        return current_bot()

    def __getattr__(self, name: str):
        return getattr(self._client(), name)


selfBot = _SelfBot()
