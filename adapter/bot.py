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
        return getattr(self.raw, name)

    def build_segment(self, segment_type: str, data: Any = None) -> Any:
        return self.adapter.build_segment(self.raw, segment_type, data)

    def build_message(self, *segments: Any) -> Any:
        return self.adapter.build_message(
            self.raw, [segment for segment in segments if segment is not None]
        )

    def message_segment_class(self) -> type | None:
        return self.adapter.message_segment_class()

    def is_mention_segment(self, segment: Any) -> bool:
        return self.adapter.is_mention_segment(segment)

    def mention_target(self, segment: Any) -> str | None:
        return self.adapter.mention_target(segment)

    async def send_group_message(self, group_id: str, message: Any):
        return await self.adapter.send_group_message(self.raw, str(group_id), message)

    async def send_private_message(self, user_id: str, message: Any):
        return await self.adapter.send_private_message(self.raw, str(user_id), message)

    async def send(self, event: Any, message: Any):
        """按当前事件上下文发送消息。"""
        return await self.adapter.send_event(self.raw, event, message)

    async def send_message_to_target(self, target: dict[str, Any], message: Any):
        return await self.adapter.send_message_to_target(self.raw, target, message)

    async def send_text_to_target(self, target: dict[str, Any], text: str):
        return await self.adapter.send_text_to_target(self.raw, target, text)

    async def send_forward_messages(
        self, event: Any, messages: list[Any], *, nickname: str = "FxBot"
    ) -> bool:
        return await self.adapter.send_forward_messages(
            self.raw, event, messages, nickname=nickname
        )

    async def get_replied_message(self, message_id: int) -> Any:
        return await self.adapter.get_replied_message(self.raw, message_id)

    def extract_image_sources(self, message: Any) -> list[str]:
        return self.adapter.extract_image_sources(message)

    def extract_reply_message_id(self, message: Any) -> int | None:
        return self.adapter.extract_reply_message_id(message)

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
    raise RuntimeError("当前事件未绑定 Bot；请仅在事件处理上下文中使用 selfBot")


class _SelfBot:
    """当前事件 Bot 代理，业务层直接使用 selfBot。"""

    def _client(self) -> PlatformBot:
        return current_bot()

    def __getattr__(self, name: str):
        return getattr(self._client(), name)


selfBot = _SelfBot()
